const RUN_STATUSES = [
  "CREATED",
  "RUNNING",
  "COMPLETED",
  "UNAVAILABLE",
  "EXPIRED",
  "DENIED",
  "WAIT_FOR_DATA",
  "INVALID",
  "FAILED",
  "TIMEOUT",
  "UNCERTAIN",
] as const;

type RunStatus = (typeof RUN_STATUSES)[number];

export type AgentOperationalQualityRow = {
  agent_id: string;
  agent_version: string;
  manifest_ref: string;
  run_count: number;
  status_counts: Record<RunStatus, number>;
  completion_rate: string;
  timing_sample_count: number;
  mean_duration_seconds: string | null;
  p95_duration_seconds: string | null;
};

export type AgentOperationalQuality = {
  schema_version: "1.0";
  as_of: string;
  availability: "AVAILABLE" | "NO_OBSERVATIONS" | "UNAVAILABLE";
  reason: "NO_GRAPH_RUNS" | "GRAPH_LEDGER_UNAVAILABLE" | "GRAPH_RUN_CAPACITY_EXCEEDED" | null;
  source_receipt_count: number;
  source_receipts_sha256: string;
  source_trace_count: number;
  source_traces_sha256: string;
  rows: AgentOperationalQualityRow[];
  metric_scope: "OPERATIONAL_OUTCOME_COUNTS";
  calibration_established: false;
  promotion_allowed: false;
};

export type AgentOperationalQualityRead =
  | { ok: true; value: AgentOperationalQuality }
  | { ok: false; reason: string };

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const integer = (value: unknown, minimum: number, maximum: number): value is number =>
  Number.isSafeInteger(value) && Number(value) >= minimum && Number(value) <= maximum;
const decimal = (value: unknown): value is string => {
  if (typeof value !== "string" || value.length > 64 || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)) return false;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0;
};
const timestamp = (value: unknown): value is string =>
  typeof value === "string" && value.length <= 40 &&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
  Number.isFinite(Date.parse(value));
const digest = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

function parseRow(value: unknown): AgentOperationalQualityRow | null {
  const keys = [
    "agent_id",
    "agent_version",
    "manifest_ref",
    "run_count",
    "status_counts",
    "completion_rate",
    "timing_sample_count",
    "mean_duration_seconds",
    "p95_duration_seconds",
  ];
  if (!record(value) || !exact(value, keys)) return null;
  if (typeof value.agent_id !== "string" || !/^A(?:0[1-9]|1[0-8])$/.test(value.agent_id)) return null;
  if (typeof value.agent_version !== "string" || !/^\d+\.\d+\.\d+$/.test(value.agent_version) || value.agent_version.length > 32) return null;
  if (typeof value.manifest_ref !== "string" || !/^agent-manifest:[a-f0-9]{64}$/.test(value.manifest_ref)) return null;
  if (!integer(value.run_count, 1, 512) || !integer(value.timing_sample_count, 0, value.run_count)) return null;
  if (!record(value.status_counts) || !exact(value.status_counts, [...RUN_STATUSES])) return null;
  const statusCounts = {} as Record<RunStatus, number>;
  for (const status of RUN_STATUSES) {
    const count = value.status_counts[status];
    if (!integer(count, 0, value.run_count)) return null;
    statusCounts[status] = count;
  }
  if (RUN_STATUSES.reduce((sum, status) => sum + statusCounts[status], 0) !== value.run_count) return null;
  if (!decimal(value.completion_rate) || Number(value.completion_rate) > 1) return null;
  if (Math.abs(Number(value.completion_rate) - statusCounts.COMPLETED / value.run_count) > Number.EPSILON * 2) return null;

  let mean: string | null = null;
  let p95: string | null = null;
  if (value.timing_sample_count === 0) {
    if (value.mean_duration_seconds !== null || value.p95_duration_seconds !== null) return null;
  } else {
    if (!decimal(value.mean_duration_seconds) || !decimal(value.p95_duration_seconds)) return null;
    mean = value.mean_duration_seconds;
    p95 = value.p95_duration_seconds;
  }
  return {
    agent_id: value.agent_id,
    agent_version: value.agent_version,
    manifest_ref: value.manifest_ref,
    run_count: value.run_count,
    status_counts: statusCounts,
    completion_rate: value.completion_rate,
    timing_sample_count: value.timing_sample_count,
    mean_duration_seconds: mean,
    p95_duration_seconds: p95,
  };
}

export function parseAgentOperationalQuality(value: unknown): AgentOperationalQuality | null {
  const keys = [
    "schema_version",
    "as_of",
    "availability",
    "reason",
    "source_receipt_count",
    "source_receipts_sha256",
    "source_trace_count",
    "source_traces_sha256",
    "rows",
    "metric_scope",
    "calibration_established",
    "promotion_allowed",
  ];
  if (!record(value) || !exact(value, keys) || value.schema_version !== "1.0" || !timestamp(value.as_of)) return null;
  if (value.metric_scope !== "OPERATIONAL_OUTCOME_COUNTS" || value.calibration_established !== false || value.promotion_allowed !== false) return null;
  if (!integer(value.source_receipt_count, 0, 512) || !integer(value.source_trace_count, 0, 512)) return null;
  if (!digest(value.source_receipts_sha256) || !digest(value.source_traces_sha256) || !Array.isArray(value.rows) || value.rows.length > 128) return null;

  const rows: AgentOperationalQualityRow[] = [];
  const identities = new Set<string>();
  let receiptTotal = 0;
  let timingTotal = 0;
  let previous = "";
  for (const item of value.rows) {
    const row = parseRow(item);
    if (!row) return null;
    const identity = `${row.agent_id}@${row.agent_version}`;
    if (identities.has(identity) || (previous && identity < previous)) return null;
    identities.add(identity);
    previous = identity;
    receiptTotal += row.run_count;
    timingTotal += row.timing_sample_count;
    rows.push(row);
  }
  if (receiptTotal !== value.source_receipt_count || timingTotal > value.source_trace_count) return null;

  const availability = value.availability;
  const reason = value.reason;
  if (availability === "AVAILABLE") {
    if (receiptTotal === 0 || reason !== null) return null;
  } else if (availability === "NO_OBSERVATIONS") {
    if (receiptTotal !== 0 || rows.length !== 0 || reason !== "NO_GRAPH_RUNS") return null;
  } else if (availability === "UNAVAILABLE") {
    if (receiptTotal !== 0 || rows.length !== 0 || (reason !== "GRAPH_LEDGER_UNAVAILABLE" && reason !== "GRAPH_RUN_CAPACITY_EXCEEDED")) return null;
  } else {
    return null;
  }

  return {
    schema_version: "1.0",
    as_of: value.as_of,
    availability,
    reason,
    source_receipt_count: value.source_receipt_count,
    source_receipts_sha256: value.source_receipts_sha256,
    source_trace_count: value.source_trace_count,
    source_traces_sha256: value.source_traces_sha256,
    rows,
    metric_scope: "OPERATIONAL_OUTCOME_COUNTS",
    calibration_established: false,
    promotion_allowed: false,
  };
}

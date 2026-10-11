const counts = ["observed_spans", "acknowledged_spans", "unconfirmed_spans", "dropped_spans", "pending_spans", "inflight_spans", "acknowledged_batches", "failed_batches", "instrumentation_errors"] as const;
const metadata = ["runtime_id", "sdk_version", "exporter_kind", "worker_alive", "queue_capacity", "batch_capacity"] as const;
export type TracingStatus = {
  schema_version: "1.0"; mode: "PAPER"; accounting: "VOLATILE_FINISHED_SDK_SPANS";
  durable: false; delivery_verified: false; observed_at: string;
  runtime_state: "DISABLED" | "UNAVAILABLE" | "RUNNING" | "STOPPING" | "STOPPED" | "FAILED";
  runtime_id: string | null; sdk_version: string | null;
  exporter_kind: "LOCAL_OTLP_PROTOBUF" | "INJECTED" | null; worker_alive: boolean | null;
  queue_capacity: 512 | null; batch_capacity: 32 | null;
} & Record<typeof counts[number], number | null>;

function utc(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)
    && Number(value.slice(0, 4)) >= 1 && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString().slice(0, 19) === value.slice(0, 19);
}
export function parseTracingStatus(value: unknown): TracingStatus | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  if (v.schema_version !== "1.0" || v.mode !== "PAPER" || v.accounting !== "VOLATILE_FINISHED_SDK_SPANS"
    || v.durable !== false || v.delivery_verified !== false || !utc(v.observed_at)
    || !["DISABLED", "UNAVAILABLE", "RUNNING", "STOPPING", "STOPPED", "FAILED"].includes(v.runtime_state as string)) return null;
  const empty = v.runtime_state === "DISABLED" || v.runtime_state === "UNAVAILABLE";
  if (empty) {
    if ([...metadata, ...counts].some(key => v[key] !== null)) return null;
  } else {
    if (typeof v.runtime_id !== "string" || !/^[a-f0-9]{32}$/.test(v.runtime_id)
      || typeof v.sdk_version !== "string" || !/^\d{1,4}\.\d{1,4}\.\d{1,4}$/.test(v.sdk_version)
      || !["LOCAL_OTLP_PROTOBUF", "INJECTED"].includes(v.exporter_kind as string)
      || typeof v.worker_alive !== "boolean" || v.queue_capacity !== 512 || v.batch_capacity !== 32
      || counts.some(key => typeof v[key] !== "number" || !Number.isSafeInteger(v[key]) || (v[key] as number) < 0)) return null;
    const n = (key: typeof counts[number]) => v[key] as number;
    const total = ["acknowledged_spans", "unconfirmed_spans", "dropped_spans", "pending_spans", "inflight_spans"] as const;
    if (BigInt(n("observed_spans")) !== total.reduce((sum, key) => sum + BigInt(n(key)), BigInt(0))
      || n("pending_spans") > 512 || n("inflight_spans") > 32) return null;
    for (const [spans, batches] of [["acknowledged_spans", "acknowledged_batches"], ["unconfirmed_spans", "failed_batches"]] as const) {
      if (n(spans) < n(batches) || BigInt(n(spans)) > BigInt(32) * BigInt(n(batches))) return null;
    }
    if (v.runtime_state === "RUNNING" && !v.worker_alive
      || v.runtime_state === "STOPPED" && (v.worker_alive || n("pending_spans") || n("inflight_spans"))
      || v.runtime_state === "STOPPING" && (!v.worker_alive || n("pending_spans"))
      || v.runtime_state === "FAILED" && (n("pending_spans") || n("inflight_spans"))) return null;
  }
  return Object.fromEntries(["schema_version", "mode", "accounting", "durable", "delivery_verified", "observed_at", "runtime_state", ...metadata, ...counts].map(key => [key, v[key]])) as TracingStatus;
}

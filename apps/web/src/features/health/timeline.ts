export type HealthStatus = {
  state: "DISABLED" | "RESTORED_ONLY" | "MONITORING" | "ERROR";
  enabled: boolean;
  open_incidents: number | null;
  quarantined_versions: string[];
  last_tick: string | null;
  last_error: string | null;
};

export type Incident = {
  schema_version: "1.0"; incident_id: string; code: string; subject: string;
  detected_at: string; agent_id: string | null; agent_version: string | null;
  manifest_ref: string | null; safety_state: "HALT" | "NO_NEW_RISK";
  quarantine: boolean; status: "OPEN" | "CLOSED"; transitions: IncidentTransition[];
  promotion_allowed: false;
};

export type IncidentTransition = {
  action: "CLOSED" | "REOPENED"; at: string; command_id: string | null;
  incident_id: string; reason: string | null; transition_id: string;
};

export type Timeline = {
  head_hash: string; total: number; offset: number; limit: number;
  next_offset: number | null; incidents: Incident[];
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function bounded(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= maximum;
}
function utc(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$/.test(value) && Number.isFinite(Date.parse(value));
}
function count(value: unknown, maximum: number): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= maximum;
}
function digest(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

export function parseHealth(value: unknown): HealthStatus | null {
  if (!record(value) || value.schema_version !== "1.0") return null;
  if ((value.mode !== undefined && value.mode !== "PAPER") || (value.promotion_allowed !== undefined && value.promotion_allowed !== false)) return null;
  if (value.state === "DISABLED" && value.enabled === false) return { state: "DISABLED", enabled: false, open_incidents: null, quarantined_versions: [], last_tick: null, last_error: null };
  if (value.mode !== "PAPER" || value.promotion_allowed !== false || !count(value.open_incidents, 1000) || !Array.isArray(value.quarantined_versions) || value.quarantined_versions.length > 1000 || value.quarantined_versions.some((pin) => typeof pin !== "string" || !/^A(0[1-9]|1[0-8])@\d+\.\d+\.\d+$/.test(pin) || pin.length > 36) || new Set(value.quarantined_versions).size !== value.quarantined_versions.length) return null;
  const state = value.state;
  if (state !== "RESTORED_ONLY" && state !== "MONITORING" && state !== "ERROR") return null;
  if (value.enabled !== (state !== "RESTORED_ONLY") || (value.last_tick !== null && !utc(value.last_tick)) || (value.last_error !== null && value.last_error !== "WATCHDOG_VERIFICATION_OR_STORAGE_FAILURE")) return null;
  return { state, enabled: value.enabled, open_incidents: value.open_incidents, quarantined_versions: [...value.quarantined_versions], last_tick: value.last_tick, last_error: value.last_error };
}

const halted = ["AUDIT_INVALID", "GRAPH_INTEGRITY_FAILED", "POSITION_MISMATCH", "WATCHDOG_FAILURE", "CLOCK_REGRESSION"];
const stopped = ["BROKER_UNCONFIRMED", "QUOTE_STALE", "QUOTE_FUTURE", "QUOTE_UNTRUSTED", "AGENT_FAILED", "AGENT_TIMEOUT", "AGENT_INVALID", "AGENT_UNCERTAIN"];

function parseIncident(value: unknown): Incident | null {
  if (!record(value) || value.schema_version !== "1.0" || !digest(value.incident_id) || !bounded(value.subject, 128) || !utc(value.detected_at) || typeof value.code !== "string" || (!halted.includes(value.code) && !stopped.includes(value.code)) || (value.status !== "OPEN" && value.status !== "CLOSED") || value.promotion_allowed !== false || !Array.isArray(value.transitions) || value.transitions.length > 10000) return null;
  const safety = halted.includes(value.code) ? "HALT" : "NO_NEW_RISK";
  const quarantine = value.code.startsWith("AGENT_");
  if (value.safety_state !== safety || value.quarantine !== quarantine) return null;
  let agentId: string | null = null, version: string | null = null, manifest: string | null = null;
  if (quarantine) {
    if (!bounded(value.agent_id, 3) || !/^A(0[1-9]|1[0-8])$/.test(value.agent_id) || !bounded(value.agent_version, 32) || !/^\d+\.\d+\.\d+$/.test(value.agent_version) || typeof value.manifest_ref !== "string" || !/^agent-manifest:[a-f0-9]{64}$/.test(value.manifest_ref)) return null;
    agentId = value.agent_id; version = value.agent_version; manifest = value.manifest_ref;
  } else if (value.agent_id !== null || value.agent_version !== null || value.manifest_ref !== null) return null;
  const transitions: IncidentTransition[] = [];
  let closed = false;
  for (const [index, raw] of value.transitions.entries()) {
    if (!record(raw) || (raw.action !== "CLOSED" && raw.action !== "REOPENED") || !utc(raw.at) || raw.incident_id !== value.incident_id || raw.transition_id !== `${value.incident_id}:${index + 1}`) return null;
    if (raw.action === "CLOSED") {
      if (closed || quarantine || !["QUOTE_STALE", "QUOTE_FUTURE", "QUOTE_UNTRUSTED"].includes(value.code) || !bounded(raw.command_id, 128) || !bounded(raw.reason, 256)) return null;
      closed = true;
    } else {
      if (!closed || raw.command_id !== null || raw.reason !== null) return null;
      closed = false;
    }
    transitions.push({ action: raw.action, at: raw.at, command_id: raw.command_id as string | null, incident_id: raw.incident_id as string, reason: raw.reason as string | null, transition_id: raw.transition_id as string });
  }
  if ((value.status === "CLOSED") !== closed) return null;
  return { schema_version: "1.0", incident_id: value.incident_id, code: value.code, subject: value.subject, detected_at: value.detected_at, agent_id: agentId, agent_version: version, manifest_ref: manifest, safety_state: safety, quarantine, status: value.status, transitions, promotion_allowed: false };
}

export function parseTimeline(value: unknown): Timeline | null {
  if (!record(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || !digest(value.head_hash) || !count(value.total, 1000) || !count(value.offset, 1000) || !count(value.limit, 100) || value.limit < 1 || !Array.isArray(value.incidents) || value.incidents.length !== Math.min(value.limit, Math.max(0, value.total - value.offset))) return null;
  const expectedNext = value.offset + value.limit < value.total ? value.offset + value.limit : null;
  if (value.next_offset !== expectedNext) return null;
  const incidents: Incident[] = [];
  const ids = new Set<string>();
  for (const item of value.incidents) {
    const incident = parseIncident(item);
    if (!incident || ids.has(incident.incident_id)) return null;
    ids.add(incident.incident_id); incidents.push(incident);
  }
  return { head_hash: value.head_hash, total: value.total, offset: value.offset, limit: value.limit, next_offset: expectedNext, incidents };
}

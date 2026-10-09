type ObjectValue = Record<string, unknown>;
export type DemoControllerHealth = {
  state: string; code: string | null; revision: number; observed_at: string;
  next_due_at: string; last_history_end: string | null; last_success_at: string | null;
  consecutive_failures: number; pending_attempt_id: string | null;
  pending_history_start: string | null; pending_history_end: string | null;
};
export type DemoServiceRecord = {
  run_id: string; state: string; code: string | null; ticks: number;
  published_at: string; valid_until: string; health: DemoControllerHealth | null;
};
export type DemoHealthRead = { configured: boolean; state: "AVAILABLE" | "STALE" | "UNAVAILABLE"; record: DemoServiceRecord | null };
function object(value: unknown): value is ObjectValue { return typeof value === "object" && value !== null && !Array.isArray(value); }
function denied(value: ObjectValue) { return value.may_submit_orders === false && value.may_affect_portfolio === false; }
function count(value: unknown, maximum: number): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= maximum; }
function id(value: unknown): value is string { return typeof value === "string" && /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value); }
function micros(value: unknown): bigint | null {
  if (typeof value !== "string") return null;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(value);
  if (!match) return null;
  const time = Date.parse(match[1] + "Z");
  if (!Number.isFinite(time) || new Date(time).toISOString().slice(0,19) !== match[1]) return null;
  return BigInt(time) * BigInt(1000) + BigInt((match[2] ?? "").padEnd(6,"0"));
}
function stamp(value: unknown): value is string { return micros(value) !== null; }
function nullableStamp(value: unknown): value is string | null { return value === null || stamp(value); }
const healthCodes = ["STORAGE_UNAVAILABLE", "CLOCK_REGRESSION", "HISTORY_GAP", "CUSTODY_UNCERTAIN", "SOURCE_FAILURE", "PROJECTION_FAILURE", "CAPACITY"];
function controller(value: unknown): DemoControllerHealth | null {
  if (!object(value) || value.schema_version !== "1.0" || value.observation_kind !== "DEMO_SYNC_HEALTH" || value.native_attestation !== "UNVERIFIED" || !denied(value)) return null;
  if (typeof value.state !== "string" || !["DISABLED","STARTING","ACTIVE","HEALTHY","DEGRADED","HALTED"].includes(value.state) || !(value.code === null || (typeof value.code === "string" && healthCodes.includes(value.code)))) return null;
  if (!count(value.revision,3000) || !count(value.consecutive_failures,3000) || !stamp(value.observed_at) || !stamp(value.next_due_at) || !nullableStamp(value.last_history_end) || !nullableStamp(value.last_success_at) || !nullableStamp(value.pending_history_start) || !nullableStamp(value.pending_history_end) || !(value.pending_attempt_id === null || id(value.pending_attempt_id))) return null;
  const pending = value.pending_attempt_id !== null;
  if (pending !== (value.pending_history_start !== null) || pending !== (value.pending_history_end !== null) || (value.state === "ACTIVE" && !pending) || (pending && !["ACTIVE","HALTED"].includes(value.state))) return null;
  if (pending) {
    const width = micros(value.pending_history_end)! - micros(value.pending_history_start)!;
    if (width <= BigInt(0) || width > BigInt(86400000000)) return null;
  }
  if ((value.last_success_at !== null && micros(value.last_success_at)! > micros(value.observed_at)!) || (value.last_history_end !== null && micros(value.last_history_end)! > micros(value.observed_at)!)) return null;
  return { state:value.state, code:value.code, revision:value.revision, observed_at:value.observed_at, next_due_at:value.next_due_at, last_history_end:value.last_history_end, last_success_at:value.last_success_at, consecutive_failures:value.consecutive_failures, pending_attempt_id:value.pending_attempt_id, pending_history_start:value.pending_history_start, pending_history_end:value.pending_history_end };
}
export function parseDemoSyncHealth(value: unknown, observedAt: string): DemoHealthRead | null {
  const now = micros(observedAt);
  if (now === null || !object(value) || value.schema_version !== "1.0" || value.source_kind !== "LOCAL_UNVERIFIED_HEARTBEAT" || typeof value.configured !== "boolean" || !denied(value) || !object(value.observation) || !denied(value.observation)) return null;
  const observation = value.observation;
  if (observation.state === "UNAVAILABLE") return observation.record === null ? {configured:value.configured,state:"UNAVAILABLE",record:null} : null;
  if (!value.configured || !["AVAILABLE","STALE"].includes(String(observation.state)) || !object(observation.record)) return null;
  const record = observation.record;
  if (record.schema_version !== "1.0" || record.observation_kind !== "DEMO_SYNC_SERVICE" || record.entry_point !== "DEMO_SYNC_SERVICE" || record.native_attestation !== "UNVERIFIED" || !denied(record) || !id(record.run_id) || !count(record.ticks,10000) || !stamp(record.published_at) || !stamp(record.valid_until)) return null;
  if (typeof record.state !== "string" || !["RUNNING","STOPPED","HALTED","DISABLED"].includes(record.state) || !(record.code === null || record.code === "TICK_LIMIT" || record.code === "SERVICE_FAILURE")) return null;
  const published = micros(record.published_at)!, expiry = micros(record.valid_until)!;
  if (published > now || expiry - published !== BigInt(30000000)) return null;
  const health = record.health === null ? null : controller(record.health);
  if (record.health !== null && health === null) return null;
  if (health && micros(health.observed_at)! > published) return null;
  return {configured:true,state:now >= expiry || observation.state === "STALE" ? "STALE" : "AVAILABLE",record:{run_id:record.run_id,state:record.state,code:record.code,ticks:record.ticks,published_at:record.published_at,valid_until:record.valid_until,health}};
}

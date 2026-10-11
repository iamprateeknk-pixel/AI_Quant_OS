export const advisoryRunPattern = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
type Binding = {
  schema_version: "1.0"; mode: "PAPER"; kind: "OBSERVED_ARCHIVE_SDK_CONTEXT"; delivery_verified: false;
  run_id: string; run_sha256: string; runtime_id: string; sdk_version: string; sdk_trace_id: string; sdk_span_id: string; recorded_at: string;
};
export type AgentTraceLookup = {
  schema_version: "1.0"; mode: "PAPER"; delivery_verified: false; independently_attested: false;
  run_id: string; observed_at: string; state: "AVAILABLE" | "NOT_RECORDED" | "NOT_FOUND" | "UNAVAILABLE";
  source_head: string | null; source_event_count: number | null; binding: Binding | null;
};
const object = (value: unknown): Record<string, unknown> | null => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const hex = (value: unknown, size: number, nonzero = false): value is string => typeof value === "string" && new RegExp(`^[a-f0-9]{${size}}$`).test(value) && (!nonzero || /[1-9a-f]/.test(value));
function instant(value: unknown): bigint | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value) || Number(value.slice(0, 4)) < 1) return null;
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds) || new Date(value).toISOString().slice(0, 19) !== value.slice(0, 19)) return null;
  const fraction = /\.(\d+)/.exec(value)?.[1] ?? "";
  return BigInt(milliseconds) * BigInt(1000) + BigInt(Number(fraction.padEnd(6, "0")) % 1000);
}
export function parseAgentTraceLookup(value: unknown, requested: string): AgentTraceLookup | null {
  const v = object(value);
  if (!v || !advisoryRunPattern.test(requested) || v.run_id !== requested || v.schema_version !== "1.0" || v.mode !== "PAPER" || v.delivery_verified !== false || v.independently_attested !== false || !["AVAILABLE", "NOT_RECORDED", "NOT_FOUND", "UNAVAILABLE"].includes(v.state as string)) return null;
  const observed = instant(v.observed_at);
  if (observed === null) return null;
  if (v.state === "UNAVAILABLE") {
    if (v.source_head !== null || v.source_event_count !== null || v.binding !== null) return null;
  } else if (!hex(v.source_head, 64) || typeof v.source_event_count !== "number" || !Number.isSafeInteger(v.source_event_count) || v.source_event_count < (v.state === "AVAILABLE" ? 2 : v.state === "NOT_RECORDED" ? 1 : 0)) return null;
  let binding: Binding | null = null;
  if (v.state === "AVAILABLE") {
    const b = object(v.binding), recorded = instant(b?.recorded_at);
    if (!b || b.schema_version !== "1.0" || b.mode !== "PAPER" || b.kind !== "OBSERVED_ARCHIVE_SDK_CONTEXT" || b.delivery_verified !== false || b.run_id !== requested || !hex(b.run_sha256, 64) || !hex(b.runtime_id, 32, true) || !hex(b.sdk_trace_id, 32, true) || !hex(b.sdk_span_id, 16, true) || typeof b.sdk_version !== "string" || !/^\d{1,4}\.\d{1,4}\.\d{1,4}$/.test(b.sdk_version) || recorded === null || recorded > observed) return null;
    binding = Object.fromEntries(["schema_version", "mode", "kind", "delivery_verified", "run_id", "run_sha256", "runtime_id", "sdk_version", "sdk_trace_id", "sdk_span_id", "recorded_at"].map(key => [key, b[key]])) as Binding;
  } else if (v.binding !== null) return null;
  return {...Object.fromEntries(["schema_version", "mode", "delivery_verified", "independently_attested", "run_id", "observed_at", "state", "source_head", "source_event_count"].map(key => [key, v[key]])), binding} as AgentTraceLookup;
}

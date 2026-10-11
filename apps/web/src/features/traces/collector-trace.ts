const names = ["paper.http", "paper.market.snapshot", "paper.risk.evaluate", "paper.order.submit", "paper.order.replay", "paper.order.recover", "paper.agent.classify", "paper.agent.review", "paper.agent.archive", "paper.provider.classify", "paper.provider.review"] as const;
const stringFields = ["http_method", "http_route", "risk_decision", "order_id", "order_status", "envelope_id", "parent_envelope_id", "agent_route", "review_status"] as const;
export type CollectorSpan = {trace_id: string; span_id: string; parent_span_id: string | null; name: typeof names[number]; start_ns: string; end_ns: string; status: "UNSET" | "OK" | "ERROR"; http_status: number | null} & Record<typeof stringFields[number], string | null>;
export type CollectorTrace = {schema_version: "1.0"; mode: "PAPER"; delivery_verified: false; independently_attested: false; complete_trace: false; trace_id: string; observed_at: string; state: "RECORDED_SPANS" | "NOT_OBSERVED" | "DISABLED" | "UNAVAILABLE"; spans: CollectorSpan[]; files: {ordinal: number; prefix_bytes: number; prefix_sha256: string}[]};
const object = (v: unknown): Record<string, unknown> | null => v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null;
const hex = (v: unknown, size: number): v is string => typeof v === "string" && new RegExp(`^[a-f0-9]{${size}}$`).test(v);
export const validSdkTraceId = (v: unknown): v is string => hex(v, 32) && /[1-9a-f]/.test(v);
const nonzeroSpan = (v: unknown): v is string => hex(v, 16) && /[1-9a-f]/.test(v);
const nanos = (v: unknown): v is string => typeof v === "string" && /^[1-9][0-9]{0,18}$/.test(v) && BigInt(v) <= BigInt("9223372036854775807");
function utc(v: unknown): v is string {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v) && Number(v.slice(0,4)) >= 1 && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0,19) === v.slice(0,19);
}
function span(v: unknown, trace: string): CollectorSpan | null {
  const s = object(v);
  if (!s || s.trace_id !== trace || !nonzeroSpan(s.span_id) || (s.parent_span_id !== null && !nonzeroSpan(s.parent_span_id)) || s.parent_span_id === s.span_id || !names.includes(s.name as typeof names[number]) || !nanos(s.start_ns) || !nanos(s.end_ns) || BigInt(s.end_ns) < BigInt(s.start_ns) || !["UNSET", "OK", "ERROR"].includes(s.status as string)) return null;
  const enums: Record<string, string[]> = {http_method:["GET","POST","DELETE","PUT","PATCH","OTHER"], risk_decision:["APPROVE","REJECT","HALT"], order_status:["PENDING","DISPATCHING","ACCEPTED","REJECTED","UNKNOWN","FILLED","PARTIALLY_FILLED"], agent_route:["DEEP_REVIEW","WAIT_FOR_DATA"], review_status:["COMPLETED","NOT_CONFIGURED","WAIT_FOR_DATA","BUSY","TIMEOUT","FAILED","INVALID"]};
  for (const key of stringFields) {
    const value = s[key];
    if (value === null) continue;
    if (typeof value !== "string" || (enums[key] && !enums[key].includes(value))) return null;
    if (["order_id","envelope_id","parent_envelope_id"].includes(key) && !/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value)) return null;
    if (key === "http_route" && !/^(UNMATCHED|\/api\/v1\/[A-Za-z0-9_/{}/.-]{1,240})$/.test(value)) return null;
  }
  if (s.http_status !== null && (typeof s.http_status !== "number" || !Number.isInteger(s.http_status) || s.http_status < 100 || s.http_status > 599)) return null;
  return Object.fromEntries(["trace_id","span_id","parent_span_id","name","start_ns","end_ns","status","http_status",...stringFields].map(key=>[key,s[key]])) as CollectorSpan;
}
export function parseCollectorTrace(value: unknown, requested: string): CollectorTrace | null {
  const v = object(value);
  if (!v || !validSdkTraceId(requested) || v.trace_id !== requested || v.schema_version !== "1.0" || v.mode !== "PAPER" || v.delivery_verified !== false || v.independently_attested !== false || v.complete_trace !== false || !utc(v.observed_at) || !["RECORDED_SPANS","NOT_OBSERVED","DISABLED","UNAVAILABLE"].includes(v.state as string) || !Array.isArray(v.spans) || v.spans.length > 128 || !Array.isArray(v.files) || v.files.length > 4) return null;
  const spans: CollectorSpan[] = [], files: CollectorTrace["files"] = [];
  for (const item of v.spans) {const parsed=span(item,requested);if (!parsed) return null;spans.push(parsed);}
  for (const [ordinal, item] of v.files.entries()) {
    const f=object(item);
    if (!f || f.ordinal !== ordinal || typeof f.prefix_bytes !== "number" || !Number.isSafeInteger(f.prefix_bytes) || f.prefix_bytes < 0 || f.prefix_bytes > 33554432 || !hex(f.prefix_sha256,64)) return null;
    files.push({ordinal,prefix_bytes:f.prefix_bytes,prefix_sha256:f.prefix_sha256});
  }
  if (["DISABLED","UNAVAILABLE"].includes(v.state as string)) {if (spans.length || files.length) return null;}
  else if (!files.length || Boolean(spans.length) !== (v.state === "RECORDED_SPANS") || (spans.length && !files.some(f=>f.prefix_bytes>0))) return null;
  const byId=new Map(spans.map(s=>[s.span_id,s]));if (byId.size !== spans.length) return null;
  for (const s of spans) {const visited=new Set<string>();let current: CollectorSpan | undefined=s;while (current) {if(visited.has(current.span_id))return null;visited.add(current.span_id);current=current.parent_span_id?byId.get(current.parent_span_id):undefined;}}
  return {...Object.fromEntries(["schema_version","mode","delivery_verified","independently_attested","complete_trace","trace_id","observed_at","state"].map(key=>[key,v[key]])),spans,files} as CollectorTrace;
}

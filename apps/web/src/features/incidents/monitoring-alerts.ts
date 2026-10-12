const names = ["PaperHttpScrapeFailed", "PaperHttpScrapeMissing"] as const;
const paths = ["docs/runbooks/paper-http-monitoring.md#scrape-failed", "docs/runbooks/paper-http-monitoring.md#scrape-missing"] as const;
type Rule = {name: typeof names[number]; state: "INACTIVE" | "PENDING" | "FIRING"; evaluated_at: string; active_since: string | null; hold_seconds: 120; severity: "warning"; runbook_path: typeof paths[number]};
export type MonitoringAlerts = {schema_version: "1.0"; mode: "PAPER"; source_kind: "LOCAL_PROMETHEUS_RULE_EVALUATION"; durable: false; notification_verified: false; independently_attested: false; observed_at: string; state: "AVAILABLE" | "DISABLED" | "UNAVAILABLE"; rules: Rule[]};
const object = (value: unknown): Record<string, unknown> | null => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
function micros(value: unknown): bigint | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value) || Number(value.slice(0,4)) < 1 || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0,19) !== value.slice(0,19)) return null;
  const fraction = value.match(/\.(\d{1,6})/)?.[1] ?? "";
  return BigInt(Date.parse(value.slice(0,19)+"Z"))*BigInt(1000) + BigInt(fraction.padEnd(6,"0"));
}
export function parseMonitoringAlerts(value: unknown): MonitoringAlerts | null {
  const v = object(value), captured = micros(v?.observed_at);
  if (!v || captured === null || v.schema_version !== "1.0" || v.mode !== "PAPER" || v.source_kind !== "LOCAL_PROMETHEUS_RULE_EVALUATION" || v.durable !== false || v.notification_verified !== false || v.independently_attested !== false || !["AVAILABLE","DISABLED","UNAVAILABLE"].includes(v.state as string) || !Array.isArray(v.rules) || v.rules.length !== (v.state === "AVAILABLE" ? 2 : 0)) return null;
  const rules: Rule[] = [];
  for (const [index, item] of v.rules.entries()) {
    const r = object(item), evaluated = micros(r?.evaluated_at), active = r?.active_since === null ? null : micros(r?.active_since);
    if (!r || r.name !== names[index] || r.runbook_path !== paths[index] || !["INACTIVE","PENDING","FIRING"].includes(r.state as string) || r.hold_seconds !== 120 || r.severity !== "warning" || evaluated === null || captured < evaluated || captured-evaluated > BigInt(45000000) || (r.active_since === null) !== (r.state === "INACTIVE") || (r.state !== "INACTIVE" && (active === null || active > evaluated || (r.state === "FIRING" && evaluated-active < BigInt(120000000))))) return null;
    rules.push({name: r.name as Rule["name"], state: r.state as Rule["state"], evaluated_at: r.evaluated_at as string, active_since: r.active_since as string | null, hold_seconds: 120, severity: "warning", runbook_path: r.runbook_path as Rule["runbook_path"]});
  }
  return {schema_version:"1.0",mode:"PAPER",source_kind:"LOCAL_PROMETHEUS_RULE_EVALUATION",durable:false,notification_verified:false,independently_attested:false,observed_at:v.observed_at as string,state:v.state as MonitoringAlerts["state"],rules};
}

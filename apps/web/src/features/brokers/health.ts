export type BrokerHealthView = {
  broker_id: "PAPER" | "EXNESS_MT5_DEMO" | "EXNESS" | "IBKR" | "ZERODHA";
  connection_state: "CONNECTED" | "DISCONNECTED" | "UNKNOWN";
  quote_age_ms: number | null;
  order_latency_ms: number | null;
  reject_rate: number | null;
  reconciliation_state: "RECONCILED" | "MISMATCH" | "UNASSESSED";
  spread_state: "WITHIN_LIMIT" | "OUTSIDE_LIMIT" | "UNASSESSED";
  account_state: "AVAILABLE" | "UNAVAILABLE" | "UNASSESSED";
  health_score: number | null;
  updated_at: string;
};

const ids: BrokerHealthView["broker_id"][] = ["PAPER", "EXNESS_MT5_DEMO", "EXNESS", "IBKR", "ZERODHA"];
const states = {
  connection_state: ["CONNECTED", "DISCONNECTED", "UNKNOWN"],
  reconciliation_state: ["RECONCILED", "MISMATCH", "UNASSESSED"],
  spread_state: ["WITHIN_LIMIT", "OUTSIDE_LIMIT", "UNASSESSED"],
  account_state: ["AVAILABLE", "UNAVAILABLE", "UNASSESSED"],
} as const;
function object(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function metric(value: unknown): value is number | null { return value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0); }
function rate(value: unknown): value is number | null { return value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1); }

export function parseBrokerHealth(value: unknown): BrokerHealthView[] | null {
  if (!object(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || value.live_execution_released !== false || !Array.isArray(value.brokers) || value.brokers.length !== ids.length) return null;
  const rows: BrokerHealthView[] = [];
  const seen = new Set<string>();
  for (const item of value.brokers) {
    if (!object(item) || !ids.includes(item.broker_id as BrokerHealthView["broker_id"]) || seen.has(item.broker_id as string)) return null;
    if (item.schema_version !== "1.0" || item.mode !== "PAPER" || item.live_execution_released !== false || !Object.hasOwn(item, "connection_state") || !Object.hasOwn(item, "updated_at")) return null;
    if (!(states.connection_state as readonly unknown[]).includes(item.connection_state) || !(states.reconciliation_state as readonly unknown[]).includes(item.reconciliation_state) || !(states.spread_state as readonly unknown[]).includes(item.spread_state) || !(states.account_state as readonly unknown[]).includes(item.account_state)) return null;
    if (!metric(item.quote_age_ms) || !metric(item.order_latency_ms) || !rate(item.reject_rate) || !rate(item.health_score) || typeof item.updated_at !== "string" || !Number.isFinite(Date.parse(item.updated_at))) return null;
    if (item.health_score !== null && (item.quote_age_ms === null || item.order_latency_ms === null || item.reject_rate === null || item.connection_state === "UNKNOWN" || item.reconciliation_state === "UNASSESSED" || item.spread_state === "UNASSESSED" || item.account_state === "UNASSESSED")) return null;
    seen.add(item.broker_id as string);
    rows.push({broker_id:item.broker_id as BrokerHealthView["broker_id"],connection_state:item.connection_state as BrokerHealthView["connection_state"],quote_age_ms:item.quote_age_ms,order_latency_ms:item.order_latency_ms,reject_rate:item.reject_rate,reconciliation_state:item.reconciliation_state as BrokerHealthView["reconciliation_state"],spread_state:item.spread_state as BrokerHealthView["spread_state"],account_state:item.account_state as BrokerHealthView["account_state"],health_score:item.health_score,updated_at:item.updated_at});
  }
  return rows.sort((a,b)=>ids.indexOf(a.broker_id)-ids.indexOf(b.broker_id));
}

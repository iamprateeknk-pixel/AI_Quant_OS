import "server-only";

export type SystemStatus = { mode: "PAPER"; safety_state: string; order_count: number };
export type PaperOrder = { order_id: string; intent_id: string; risk_id: string; status: string; broker_route: "PAPER" };
export type AuditEvent = { action: string; intent_id: string; reason: string; trace_id?: string };
export type BrokerView = { broker_id: string; connection_state: string; enabled: boolean; instruments: string[] };
export type MarketView = { instrument_id: string; bid: string; ask: string; observed_at: string; world_state_version: string; freshness: string; source: string };
export type ReadResult<T> = { ok: true; value: T } | { ok: false; reason: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function read<T>(path: string, parse: (value: unknown) => T | null): Promise<ReadResult<T>> {
  const base = process.env.AI_QUANT_API_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${base}${path}`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    if (!response.ok) return { ok: false, reason: `API returned ${response.status}` };
    const value = parse(await response.json());
    return value === null ? { ok: false, reason: "Invalid API response" } : { ok: true, value };
  } catch {
    return { ok: false, reason: "Control API unavailable" };
  }
}

export function getSystemStatus(): Promise<ReadResult<SystemStatus>> {
  return read("/api/v1/system/status", (value) => {
    if (!isRecord(value) || value.mode !== "PAPER" || typeof value.safety_state !== "string" || typeof value.order_count !== "number") return null;
    return { mode: "PAPER", safety_state: value.safety_state, order_count: value.order_count };
  });
}

export function getOrders(): Promise<ReadResult<{ orders: PaperOrder[] }>> {
  return read("/api/v1/orders", (value) => {
    if (!isRecord(value) || !Array.isArray(value.orders)) return null;
    const orders: PaperOrder[] = [];
    for (const item of value.orders) {
      if (!isRecord(item) || typeof item.order_id !== "string" || typeof item.intent_id !== "string" || typeof item.risk_id !== "string" || typeof item.status !== "string" || item.broker_route !== "PAPER") return null;
      orders.push({ order_id: item.order_id, intent_id: item.intent_id, risk_id: item.risk_id, status: item.status, broker_route: "PAPER" });
    }
    return { orders };
  });
}

export function getAudit(): Promise<ReadResult<{ events: AuditEvent[] }>> {
  return read("/api/v1/audit", (value) => {
    if (!isRecord(value) || !Array.isArray(value.events)) return null;
    const events: AuditEvent[] = [];
    for (const item of value.events) {
      if (!isRecord(item) || typeof item.action !== "string" || typeof item.intent_id !== "string" || typeof item.reason !== "string") return null;
      events.push({ action: item.action, intent_id: item.intent_id, reason: item.reason, trace_id: typeof item.trace_id === "string" ? item.trace_id : undefined });
    }
    return { events };
  });
}

export function getBrokers(): Promise<ReadResult<{ brokers: BrokerView[] }>> {
  return read("/api/v1/brokers", (value) => {
    if (!isRecord(value) || !Array.isArray(value.brokers)) return null;
    const brokers: BrokerView[] = [];
    for (const item of value.brokers) {
      if (!isRecord(item) || typeof item.broker_id !== "string" || typeof item.connection_state !== "string" || typeof item.enabled !== "boolean" || !Array.isArray(item.instruments) || !item.instruments.every((instrument) => typeof instrument === "string")) return null;
      brokers.push({ broker_id: item.broker_id, connection_state: item.connection_state, enabled: item.enabled, instruments: item.instruments });
    }
    return { brokers };
  });
}

export function getMarket(instrument: string): Promise<ReadResult<MarketView>> {
  return read(`/api/v1/markets/${encodeURIComponent(instrument)}`, (value) => {
    if (!isRecord(value) || typeof value.instrument_id !== "string" || typeof value.bid !== "string" || typeof value.ask !== "string" || typeof value.observed_at !== "string" || typeof value.world_state_version !== "string" || typeof value.freshness !== "string" || value.source !== "PAPER_SIMULATED") return null;
    return { instrument_id: value.instrument_id, bid: value.bid, ask: value.ask, observed_at: value.observed_at, world_state_version: value.world_state_version, freshness: value.freshness, source: "PAPER_SIMULATED" };
  });
}

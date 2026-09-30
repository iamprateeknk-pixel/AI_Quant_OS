import "server-only";

export type SystemStatus = { mode: "PAPER"; safety_state: string; order_count: number };
export type PaperOrder = { order_id: string; intent_id: string; risk_id: string; status: string; broker_route: "PAPER"; broker_order_id?: string };
export type AuditEvent = { action: string; intent_id: string; reason: string; trace_id?: string };
export type BrokerView = { broker_id: string; connection_state: string; enabled: boolean; instruments: string[] };
export type MarketView = { instrument_id: string; bid: string; ask: string; observed_at: string; world_state_version: string; freshness: string; source: string };
export type PaperFill = { fill_id: string; order_id: string; side: "BUY" | "SELL"; quantity: string; price: string; fee: string; observed_at: string; source: "PAPER_MANUAL" | "PAPER_BROKER" };
export type PortfolioView = { instrument_id: "EURUSD"; quote_currency: "USD"; mark_available: boolean; mark_price: string | null; net_quantity: string; cash_flow_quote: string; gross_exposure_quote: string | null; net_exposure_quote: string | null; marked_pnl_quote: string | null };
export type CurrencyExposureView = { currency: string; filled_balance: string; minimum_balance: string | null; maximum_balance: string | null; worst_case_exposure_reporting: string | null; issue: string | null };
export type CurrencyPortfolioView = { reporting_currency: string; valuation_available: boolean; marked_pnl_reporting: string | null; gross_worst_case_exposure_reporting: string | null; currencies: CurrencyExposureView[] };
export type RiskView = { safety_state: string; max_order_exposure: string; max_portfolio_exposure: string; worst_case_quantity: string; filled_net_quantity: string; pending_buy_quantity: string; pending_sell_quantity: string; portfolio_snapshot_id: string };
export type ReadResult<T> = { ok: true; value: T } | { ok: false; reason: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
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

export function getRisk(): Promise<ReadResult<RiskView>> {
  return read("/api/v1/risk", (value) => {
    if (!isRecord(value) || typeof value.safety_state !== "string" || typeof value.max_order_exposure !== "string" || typeof value.max_portfolio_exposure !== "string" || typeof value.worst_case_quantity !== "string" || typeof value.filled_net_quantity !== "string" || typeof value.pending_buy_quantity !== "string" || typeof value.pending_sell_quantity !== "string" || typeof value.portfolio_snapshot_id !== "string") return null;
    return { safety_state: value.safety_state, max_order_exposure: value.max_order_exposure, max_portfolio_exposure: value.max_portfolio_exposure, worst_case_quantity: value.worst_case_quantity, filled_net_quantity: value.filled_net_quantity, pending_buy_quantity: value.pending_buy_quantity, pending_sell_quantity: value.pending_sell_quantity, portfolio_snapshot_id: value.portfolio_snapshot_id };
  });
}

export function getOrders(): Promise<ReadResult<{ orders: PaperOrder[] }>> {
  return read("/api/v1/orders", (value) => {
    if (!isRecord(value) || !Array.isArray(value.orders)) return null;
    const orders: PaperOrder[] = [];
    for (const item of value.orders) {
      if (!isRecord(item) || typeof item.order_id !== "string" || typeof item.intent_id !== "string" || typeof item.risk_id !== "string" || typeof item.status !== "string" || item.broker_route !== "PAPER") return null;
      if (item.broker_order_id !== undefined && typeof item.broker_order_id !== "string") return null;
      orders.push({ order_id: item.order_id, intent_id: item.intent_id, risk_id: item.risk_id, status: item.status, broker_route: "PAPER", broker_order_id: item.broker_order_id });
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

export function getFills(): Promise<ReadResult<{ fills: PaperFill[] }>> {
  return read("/api/v1/fills", (value) => {
    if (!isRecord(value) || !Array.isArray(value.fills)) return null;
    const fills: PaperFill[] = [];
    for (const item of value.fills) {
      if (!isRecord(item) || typeof item.fill_id !== "string" || typeof item.order_id !== "string" || (item.side !== "BUY" && item.side !== "SELL") || typeof item.quantity !== "string" || typeof item.price !== "string" || typeof item.fee !== "string" || typeof item.observed_at !== "string" || (item.source !== "PAPER_MANUAL" && item.source !== "PAPER_BROKER")) return null;
      fills.push({ fill_id: item.fill_id, order_id: item.order_id, side: item.side, quantity: item.quantity, price: item.price, fee: item.fee, observed_at: item.observed_at, source: item.source });
    }
    return { fills };
  });
}

export function getPortfolio(): Promise<ReadResult<PortfolioView>> {
  return read("/api/v1/portfolio/snapshot", (value) => {
    if (!isRecord(value) || value.mode !== "PAPER" || value.instrument_id !== "EURUSD" || value.quote_currency !== "USD" || typeof value.mark_available !== "boolean" || !isNullableString(value.mark_price) || typeof value.net_quantity !== "string" || typeof value.cash_flow_quote !== "string" || !isNullableString(value.gross_exposure_quote) || !isNullableString(value.net_exposure_quote) || !isNullableString(value.marked_pnl_quote)) return null;
    return { instrument_id: "EURUSD", quote_currency: "USD", mark_available: value.mark_available, mark_price: value.mark_price, net_quantity: value.net_quantity, cash_flow_quote: value.cash_flow_quote, gross_exposure_quote: value.gross_exposure_quote, net_exposure_quote: value.net_exposure_quote, marked_pnl_quote: value.marked_pnl_quote };
  });
}

export function getCurrencyPortfolio(): Promise<ReadResult<CurrencyPortfolioView>> {
  return read("/api/v1/portfolio/currencies", (value) => {
    if (!isRecord(value) || value.mode !== "PAPER" || value.schema_version !== "1.0" || typeof value.reporting_currency !== "string" || typeof value.valuation_available !== "boolean" || !isNullableString(value.marked_pnl_reporting) || !isNullableString(value.gross_worst_case_exposure_reporting) || !Array.isArray(value.currencies)) return null;
    const currencies: CurrencyExposureView[] = [];
    for (const item of value.currencies) {
      if (!isRecord(item) || typeof item.currency !== "string" || typeof item.filled_balance !== "string" || !isNullableString(item.minimum_balance) || !isNullableString(item.maximum_balance) || !isNullableString(item.worst_case_exposure_reporting) || !isNullableString(item.issue)) return null;
      currencies.push({ currency: item.currency, filled_balance: item.filled_balance, minimum_balance: item.minimum_balance, maximum_balance: item.maximum_balance, worst_case_exposure_reporting: item.worst_case_exposure_reporting, issue: item.issue });
    }
    return { reporting_currency: value.reporting_currency, valuation_available: value.valuation_available, marked_pnl_reporting: value.marked_pnl_reporting, gross_worst_case_exposure_reporting: value.gross_worst_case_exposure_reporting, currencies };
  });
}

import "server-only";
import { parseMarketSnapshot, type MarketSnapshot } from "@/features/markets/snapshot";

export type SystemStatus = { mode: "PAPER"; safety_state: string; order_count: number };
export type PaperOrder = { order_id: string; intent_id: string; risk_id: string; instrument_id: string; status: string; broker_route: "PAPER"; broker_order_id?: string };
export type AuditEvent = { action: string; intent_id: string; reason: string; trace_id?: string };
export type BrokerView = { broker_id: string; connection_state: string; enabled: boolean; instruments: string[] };
export type MarketView = MarketSnapshot;
export type PaperFill = { fill_id: string; order_id: string; instrument_id: string; side: "BUY" | "SELL"; quantity: string; price: string; fee: string; observed_at: string; source: "PAPER_MANUAL" | "PAPER_BROKER" };
export type InstrumentPositionView = { instrument_id: string; base_currency: string; quote_currency: string; mark_price: string | null; net_quantity: string; cash_flow_quote: string; marked_pnl_quote: string | null };
export type CurrencyExposureView = { currency: string; filled_balance: string; minimum_balance: string | null; maximum_balance: string | null; worst_case_exposure_reporting: string | null; issue: string | null };
export type CurrencyPortfolioView = { reporting_currency: string; valuation_available: boolean; marked_pnl_reporting: string | null; gross_worst_case_exposure_reporting: string | null; currencies: CurrencyExposureView[] };
export type RiskView = { safety_state: string; max_order_exposure: string; max_portfolio_exposure: string; worst_case_quantity: string; filled_net_quantity: string; pending_buy_quantity: string; pending_sell_quantity: string; portfolio_snapshot_id: string; reporting_currency: string; max_currency_exposure_reporting: string; max_gross_currency_exposure_reporting: string };
export type ReadResult<T> = { ok: true; value: T } | { ok: false; reason: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

export async function read<T>(path: string, parse: (value: unknown) => T | null): Promise<ReadResult<T>> {
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
    if (!isRecord(value) || typeof value.safety_state !== "string" || typeof value.max_order_exposure !== "string" || typeof value.max_portfolio_exposure !== "string" || typeof value.worst_case_quantity !== "string" || typeof value.filled_net_quantity !== "string" || typeof value.pending_buy_quantity !== "string" || typeof value.pending_sell_quantity !== "string" || typeof value.portfolio_snapshot_id !== "string" || typeof value.reporting_currency !== "string" || typeof value.max_currency_exposure_reporting !== "string" || typeof value.max_gross_currency_exposure_reporting !== "string") return null;
    return { safety_state: value.safety_state, max_order_exposure: value.max_order_exposure, max_portfolio_exposure: value.max_portfolio_exposure, worst_case_quantity: value.worst_case_quantity, filled_net_quantity: value.filled_net_quantity, pending_buy_quantity: value.pending_buy_quantity, pending_sell_quantity: value.pending_sell_quantity, portfolio_snapshot_id: value.portfolio_snapshot_id, reporting_currency: value.reporting_currency, max_currency_exposure_reporting: value.max_currency_exposure_reporting, max_gross_currency_exposure_reporting: value.max_gross_currency_exposure_reporting };
  });
}

export function getOrders(): Promise<ReadResult<{ orders: PaperOrder[] }>> {
  return read("/api/v1/orders", (value) => {
    if (!isRecord(value) || !Array.isArray(value.orders)) return null;
    const orders: PaperOrder[] = [];
    for (const item of value.orders) {
      if (!isRecord(item) || typeof item.order_id !== "string" || typeof item.intent_id !== "string" || typeof item.risk_id !== "string" || typeof item.instrument_id !== "string" || typeof item.status !== "string" || item.broker_route !== "PAPER") return null;
      if (item.broker_order_id !== undefined && typeof item.broker_order_id !== "string") return null;
      orders.push({ order_id: item.order_id, intent_id: item.intent_id, risk_id: item.risk_id, instrument_id: item.instrument_id, status: item.status, broker_route: "PAPER", broker_order_id: item.broker_order_id });
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
  return read(`/api/v1/markets/${encodeURIComponent(instrument)}`, (value) => parseMarketSnapshot(value, instrument));
}

export function getFills(): Promise<ReadResult<{ fills: PaperFill[] }>> {
  return read("/api/v1/fills", (value) => {
    if (!isRecord(value) || !Array.isArray(value.fills)) return null;
    const fills: PaperFill[] = [];
    for (const item of value.fills) {
      if (!isRecord(item) || typeof item.fill_id !== "string" || typeof item.order_id !== "string" || typeof item.instrument_id !== "string" || (item.side !== "BUY" && item.side !== "SELL") || typeof item.quantity !== "string" || typeof item.price !== "string" || typeof item.fee !== "string" || typeof item.observed_at !== "string" || (item.source !== "PAPER_MANUAL" && item.source !== "PAPER_BROKER")) return null;
      fills.push({ fill_id: item.fill_id, order_id: item.order_id, instrument_id: item.instrument_id, side: item.side, quantity: item.quantity, price: item.price, fee: item.fee, observed_at: item.observed_at, source: item.source });
    }
    return { fills };
  });
}

export function getPositions(): Promise<ReadResult<{ positions: InstrumentPositionView[] }>> {
  return read("/api/v1/portfolio/positions", (value) => {
    if (!isRecord(value) || value.mode !== "PAPER" || value.schema_version !== "1.0" || !Array.isArray(value.positions)) return null;
    const positions: InstrumentPositionView[] = [];
    for (const item of value.positions) {
      if (!isRecord(item) || typeof item.instrument_id !== "string" || typeof item.base_currency !== "string" || typeof item.quote_currency !== "string" || !isNullableString(item.mark_price) || typeof item.net_quantity !== "string" || typeof item.cash_flow_quote !== "string" || !isNullableString(item.marked_pnl_quote)) return null;
      positions.push({ instrument_id: item.instrument_id, base_currency: item.base_currency, quote_currency: item.quote_currency, mark_price: item.mark_price, net_quantity: item.net_quantity, cash_flow_quote: item.cash_flow_quote, marked_pnl_quote: item.marked_pnl_quote });
    }
    return { positions };
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

export type AuditIntegrityView = { valid: boolean; event_count: number; head_hash: string; legacy_event_count: number; issue: string | null };

export function getAuditIntegrity(): Promise<ReadResult<AuditIntegrityView>> {
  return read("/api/v1/audit/integrity", (value) => {
    if (!isRecord(value) || value.schema_version !== "1.0" || typeof value.valid !== "boolean" || typeof value.event_count !== "number" || !Number.isSafeInteger(value.event_count) || value.event_count < 0 || typeof value.head_hash !== "string" || !/^[a-f0-9]{64}$/.test(value.head_hash) || typeof value.legacy_event_count !== "number" || !Number.isSafeInteger(value.legacy_event_count) || value.legacy_event_count < 0 || !isNullableString(value.issue)) return null;
    return { valid: value.valid, event_count: value.event_count, head_hash: value.head_hash, legacy_event_count: value.legacy_event_count, issue: value.issue };
  });
}

export type WorldStateInstrument = { instrument_id: string; quote_status: string; history_status: string; bar_count: number; spread_bps: string | null; return_bps: string | null; realized_volatility_bps: string | null; regime: string };
export type CurrencyTwinView = { currency: string; instrument_ids: string[]; fresh_quote_count: number; macro_available: boolean };
export type WorldStateView = { expired: boolean; world_state_version: string; as_of: string; valid_until: string; status: string; instruments: WorldStateInstrument[]; currency_twins: CurrencyTwinView[] };

export function getWorldState(): Promise<ReadResult<WorldStateView>> {
  return read("/api/v1/world-state", (value) => {
    if (!isRecord(value) || value.mode !== "PAPER" || value.schema_version !== "1.0" || value.source !== "PAPER_SIMULATED" || typeof value.world_state_version !== "string" || typeof value.as_of !== "string" || !Number.isFinite(Date.parse(value.as_of)) || typeof value.valid_until !== "string" || !Number.isFinite(Date.parse(value.valid_until)) || typeof value.status !== "string" || !["EMPTY", "DEGRADED", "READY"].includes(value.status) || !Array.isArray(value.instruments) || !Array.isArray(value.currency_twins)) return null;
    const instruments: WorldStateInstrument[] = [];
    for (const item of value.instruments) {
      if (!isRecord(item) || typeof item.instrument_id !== "string" || typeof item.quote_status !== "string" || typeof item.history_status !== "string" || typeof item.bar_count !== "number" || !Number.isSafeInteger(item.bar_count) || item.bar_count < 0 || !isNullableString(item.spread_bps) || !isNullableString(item.return_bps) || !isNullableString(item.realized_volatility_bps) || typeof item.regime !== "string") return null;
      instruments.push({ instrument_id: item.instrument_id, quote_status: item.quote_status, history_status: item.history_status, bar_count: item.bar_count, spread_bps: item.spread_bps, return_bps: item.return_bps, realized_volatility_bps: item.realized_volatility_bps, regime: item.regime });
    }
    const twins: CurrencyTwinView[] = [];
    for (const item of value.currency_twins) {
      if (!isRecord(item) || typeof item.currency !== "string" || !Array.isArray(item.instrument_ids) || !item.instrument_ids.every((id) => typeof id === "string") || typeof item.fresh_quote_count !== "number" || !Number.isSafeInteger(item.fresh_quote_count) || item.fresh_quote_count < 0 || typeof item.macro_available !== "boolean") return null;
      twins.push({ currency: item.currency, instrument_ids: item.instrument_ids, fresh_quote_count: item.fresh_quote_count, macro_available: item.macro_available });
    }
    return { expired: Date.now() >= Date.parse(value.valid_until), world_state_version: value.world_state_version, as_of: value.as_of, valid_until: value.valid_until, status: value.status, instruments, currency_twins: twins };
  });
}

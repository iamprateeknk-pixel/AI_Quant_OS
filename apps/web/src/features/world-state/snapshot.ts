export type WorldStateInstrument = {
  instrument_id: string;
  quote_status: "MISSING" | "UNTRUSTED" | "FUTURE" | "STALE" | "FRESH";
  history_status: "INSUFFICIENT" | "STALE" | "FRESH";
  bar_count: number;
  spread_bps: string | null;
  return_bps: string | null;
  realized_volatility_bps: string | null;
  regime: "UNKNOWN" | "TRENDING" | "RANGING" | "HIGH_VOLATILITY";
};

export type WorldStateView = {
  expired: boolean;
  world_state_version: string;
  as_of: string;
  valid_until: string;
  status: "EMPTY" | "DEGRADED" | "READY";
  instruments: WorldStateInstrument[];
  currency_twins: { currency: string; instrument_ids: string[]; fresh_quote_count: number; macro_available: boolean }[];
};

const instruments = ["EURUSD", "GBPUSD", "USDJPY", "EURJPY"] as const;
const topKeys = ["schema_version", "mode", "world_state_version", "as_of", "valid_until", "status", "instruments", "currency_twins", "policy_version", "source"];
const instrumentKeys = ["instrument_id", "base_currency", "quote_currency", "quote_status", "mid_price", "spread_bps", "history_status", "bar_count", "return_bps", "realized_volatility_bps", "regime", "evidence_refs"];
const currencyKeys = ["currency", "instrument_ids", "fresh_quote_count", "macro_available"];

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function timestamp(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
}

function decimal(value: unknown, allowNegative: boolean): value is string {
  return typeof value === "string" && /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value) && Number.isFinite(Number(value)) && (allowNegative || Number(value) >= 0);
}

function nullableDecimal(value: unknown, allowNegative: boolean): value is string | null {
  return value === null || decimal(value, allowNegative);
}

export function parseWorldState(value: unknown, now = Date.now()): WorldStateView | null {
  if (!record(value) || !exactKeys(value, topKeys) || value.schema_version !== "1.0" || value.mode !== "PAPER" ||
      value.source !== "PAPER_SIMULATED" || value.policy_version !== "paper-regime-v1" ||
      typeof value.world_state_version !== "string" || !/^world:[a-f0-9]{64}$/.test(value.world_state_version) ||
      !timestamp(value.as_of) || !timestamp(value.valid_until) || Date.parse(value.valid_until) <= Date.parse(value.as_of) ||
      typeof value.status !== "string" || !["EMPTY", "DEGRADED", "READY"].includes(value.status) ||
      !Array.isArray(value.instruments) || value.instruments.length !== instruments.length ||
      !Array.isArray(value.currency_twins) || value.currency_twins.length !== 4 || !Number.isFinite(now)) return null;

  const parsed: WorldStateInstrument[] = [];
  const seen = new Set<string>();
  for (const item of value.instruments) {
    if (!record(item) || !exactKeys(item, instrumentKeys) || typeof item.instrument_id !== "string" ||
        !(instruments as readonly string[]).includes(item.instrument_id) || seen.has(item.instrument_id)) return null;
    const instrumentId = item.instrument_id;
    if (item.base_currency !== instrumentId.slice(0, 3) || item.quote_currency !== instrumentId.slice(3) ||
        typeof item.quote_status !== "string" || !["MISSING", "UNTRUSTED", "FUTURE", "STALE", "FRESH"].includes(item.quote_status) ||
        typeof item.history_status !== "string" || !["INSUFFICIENT", "STALE", "FRESH"].includes(item.history_status) ||
        typeof item.bar_count !== "number" || !Number.isSafeInteger(item.bar_count) || item.bar_count < 0 || item.bar_count > 64 ||
        !nullableDecimal(item.mid_price, false) || !nullableDecimal(item.spread_bps, false) ||
        !nullableDecimal(item.return_bps, true) || !nullableDecimal(item.realized_volatility_bps, false) ||
        typeof item.regime !== "string" || !["UNKNOWN", "TRENDING", "RANGING", "HIGH_VOLATILITY"].includes(item.regime) ||
        !Array.isArray(item.evidence_refs) || item.evidence_refs.length > 2 ||
        !item.evidence_refs.every((ref) => typeof ref === "string" && /^(?:paper-quote|paper-bars):[a-f0-9]{64}$/.test(ref)) ||
        (item.quote_status === "FRESH" && (item.mid_price === null || item.spread_bps === null)) ||
        (item.quote_status === "MISSING" && (item.mid_price !== null || item.spread_bps !== null)) ||
        (item.history_status === "FRESH") !== (item.bar_count >= 3 && item.return_bps !== null && item.realized_volatility_bps !== null) ||
        (item.regime !== "UNKNOWN" && (item.quote_status !== "FRESH" || item.history_status !== "FRESH"))) return null;
    seen.add(instrumentId);
    parsed.push({instrument_id:instrumentId,quote_status:item.quote_status as WorldStateInstrument["quote_status"],history_status:item.history_status as WorldStateInstrument["history_status"],bar_count:item.bar_count,spread_bps:item.spread_bps,return_bps:item.return_bps,realized_volatility_bps:item.realized_volatility_bps,regime:item.regime as WorldStateInstrument["regime"]});
  }
  if (instruments.some((id) => !seen.has(id))) return null;

  const currencies = ["EUR", "GBP", "JPY", "USD"];
  const twins: WorldStateView["currency_twins"] = [];
  const seenCurrencies = new Set<string>();
  for (const item of value.currency_twins) {
    if (!record(item) || !exactKeys(item, currencyKeys) || typeof item.currency !== "string" ||
        !currencies.includes(item.currency) || seenCurrencies.has(item.currency) || !Array.isArray(item.instrument_ids) ||
        !item.instrument_ids.every((id) => typeof id === "string" && (instruments as readonly string[]).includes(id)) ||
        new Set(item.instrument_ids).size !== item.instrument_ids.length || typeof item.fresh_quote_count !== "number" ||
        !Number.isSafeInteger(item.fresh_quote_count) || item.fresh_quote_count > item.instrument_ids.length || typeof item.macro_available !== "boolean") return null;
    const currency = item.currency as string;
    const related = parsed.filter((row) => row.instrument_id.includes(currency)).map((row) => row.instrument_id).sort();
    const supplied = [...item.instrument_ids as string[]].sort();
    if (JSON.stringify(related) !== JSON.stringify(supplied) || item.fresh_quote_count !== parsed.filter((row) => related.includes(row.instrument_id) && row.quote_status === "FRESH").length) return null;
    seenCurrencies.add(currency);
    twins.push({currency,instrument_ids:supplied,fresh_quote_count:item.fresh_quote_count as number,macro_available:item.macro_available as boolean});
  }
  if (currencies.some((currency) => !seenCurrencies.has(currency))) return null;
  const status = value.status as WorldStateView["status"];
  if ((status === "EMPTY") !== parsed.every((item) => item.quote_status === "MISSING") ||
      (status === "READY") !== parsed.every((item) => item.regime !== "UNKNOWN")) return null;
  return {expired:now >= Date.parse(value.valid_until),world_state_version:value.world_state_version,as_of:value.as_of,valid_until:value.valid_until,status,instruments:parsed,currency_twins:twins};
}

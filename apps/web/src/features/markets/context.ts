type ReadResult<T> = { ok: true; value: T } | { ok: false; reason: string };
type Quote = { instrument_id: string; freshness: "FRESH" | "STALE"; world_state_version: string };
type WorldInstrument = {
  instrument_id: string;
  quote_status: string;
  history_status: string;
  bar_count: number;
  spread_bps: string | null;
  return_bps: string | null;
  realized_volatility_bps: string | null;
  regime: string;
};
type WorldState = { expired: boolean; world_state_version: string; instruments: WorldInstrument[] };
export type MarketContext =
  | { state: "MATCHED"; instrument_id: string; bar_count: number; spread_bps: string | null; return_bps: string | null; realized_volatility_bps: string | null; regime: string }
  | { state: "UNAVAILABLE" };

const numeric = (value: string | null, allowNegative: boolean) =>
  value === null || (/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value) && Number.isFinite(Number(value)) && (allowNegative || Number(value) >= 0));

export function selectMarketContext(
  quote: ReadResult<Quote>,
  world: ReadResult<WorldState>,
  instrument: string,
): MarketContext {
  if (!quote.ok || !world.ok || quote.value.instrument_id !== instrument || quote.value.freshness !== "FRESH" ||
      world.value.expired || quote.value.world_state_version !== world.value.world_state_version) return { state: "UNAVAILABLE" };
  const row = world.value.instruments.find((item) => item.instrument_id === instrument);
  if (!row || row.quote_status !== "FRESH" || row.history_status !== "FRESH" || !Number.isSafeInteger(row.bar_count) || row.bar_count < 0 ||
      !["UNKNOWN", "TRENDING", "RANGING", "HIGH_VOLATILITY"].includes(row.regime) ||
      !numeric(row.spread_bps, false) || !numeric(row.return_bps, true) || !numeric(row.realized_volatility_bps, false)) return { state: "UNAVAILABLE" };
  return {
    state: "MATCHED",
    instrument_id: row.instrument_id,
    bar_count: row.bar_count,
    spread_bps: row.spread_bps,
    return_bps: row.return_bps,
    realized_volatility_bps: row.realized_volatility_bps,
    regime: row.regime,
  };
}

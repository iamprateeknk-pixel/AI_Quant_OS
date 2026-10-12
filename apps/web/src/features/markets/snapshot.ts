export type MarketSnapshot = {
  instrument_id: string;
  bid: string;
  ask: string;
  observed_at: string;
  world_state_version: string;
  freshness: "FRESH" | "STALE";
  source: "PAPER_SIMULATED";
};

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function positiveDecimal(value: unknown): value is string {
  return typeof value === "string" && /^(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value) && Number.isFinite(Number(value)) && Number(value) > 0;
}

export function parseMarketSnapshot(value: unknown, instrument: string): MarketSnapshot | null {
  if (!object(value) || value.instrument_id !== instrument || !positiveDecimal(value.bid) || !positiveDecimal(value.ask) ||
      typeof value.observed_at !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value.observed_at) ||
      !Number.isFinite(Date.parse(value.observed_at)) || typeof value.world_state_version !== "string" ||
      value.world_state_version.length < 1 || value.world_state_version.length > 256 ||
      (value.freshness !== "FRESH" && value.freshness !== "STALE") || value.source !== "PAPER_SIMULATED") return null;
  return {instrument_id:value.instrument_id,bid:value.bid,ask:value.ask,observed_at:value.observed_at,world_state_version:value.world_state_version,freshness:value.freshness,source:"PAPER_SIMULATED"};
}

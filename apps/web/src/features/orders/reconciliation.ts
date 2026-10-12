export type ReconciledInstrument = {
  instrument_id: string;
  matches: boolean;
  local_net_quantity: string;
  broker_net_quantity: string;
  local_cash_flow_quote: string;
  broker_cash_flow_quote: string;
};
export type PaperReconciliation = {
  matches: boolean;
  local_net_quantity: string;
  broker_net_quantity: string;
  local_cash_flow_quote: string;
  broker_cash_flow_quote: string;
  local_only_fill_ids: string[];
  broker_only_fill_ids: string[];
  conflicting_fill_ids: string[];
  instruments: ReconciledInstrument[];
};
const instruments = ["EURUSD", "GBPUSD", "USDJPY", "EURJPY"];
function object(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function decimal(value: unknown): value is string { return typeof value === "string" && value.length <= 128 && /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value); }
function ids(value: unknown): value is string[] { return Array.isArray(value) && value.length <= 256 && value.every(item=>typeof item === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(item)) && new Set(value).size === value.length; }
function instrument(value: unknown): ReconciledInstrument | null {
  if (!object(value) || !instruments.includes(value.instrument_id as string) || typeof value.matches !== "boolean" || !decimal(value.local_net_quantity) || !decimal(value.broker_net_quantity) || !decimal(value.local_cash_flow_quote) || !decimal(value.broker_cash_flow_quote)) return null;
  return {instrument_id:value.instrument_id as string,matches:value.matches,local_net_quantity:value.local_net_quantity,broker_net_quantity:value.broker_net_quantity,local_cash_flow_quote:value.local_cash_flow_quote,broker_cash_flow_quote:value.broker_cash_flow_quote};
}
export function parsePaperReconciliation(value: unknown): PaperReconciliation | null {
  if (!object(value) || value.schema_version !== "1.1" || value.summary_instrument_id !== "EURUSD" || typeof value.matches !== "boolean" || !decimal(value.local_net_quantity) || !decimal(value.broker_net_quantity) || !decimal(value.local_cash_flow_quote) || !decimal(value.broker_cash_flow_quote) || !ids(value.local_only_fill_ids) || !ids(value.broker_only_fill_ids) || !ids(value.conflicting_fill_ids) || !Array.isArray(value.instruments) || value.instruments.length > instruments.length) return null;
  const rows: ReconciledInstrument[] = [], seen = new Set<string>();
  for (const item of value.instruments) { const row=instrument(item); if(!row || seen.has(row.instrument_id)) return null; seen.add(row.instrument_id); rows.push(row); }
  const expectedMatches = rows.every(row=>row.matches) && value.local_only_fill_ids.length===0 && value.broker_only_fill_ids.length===0 && value.conflicting_fill_ids.length===0;
  if (value.matches !== expectedMatches) return null;
  const eurusd=rows.find(row=>row.instrument_id==="EURUSD");
  if (eurusd && (eurusd.local_net_quantity!==value.local_net_quantity || eurusd.broker_net_quantity!==value.broker_net_quantity || eurusd.local_cash_flow_quote!==value.local_cash_flow_quote || eurusd.broker_cash_flow_quote!==value.broker_cash_flow_quote)) return null;
  return {matches:value.matches,local_net_quantity:value.local_net_quantity,broker_net_quantity:value.broker_net_quantity,local_cash_flow_quote:value.local_cash_flow_quote,broker_cash_flow_quote:value.broker_cash_flow_quote,local_only_fill_ids:[...value.local_only_fill_ids],broker_only_fill_ids:[...value.broker_only_fill_ids],conflicting_fill_ids:[...value.conflicting_fill_ids],instruments:rows};
}

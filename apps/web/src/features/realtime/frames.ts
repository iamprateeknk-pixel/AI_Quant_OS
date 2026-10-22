export type RealtimeFrame = {
  schema_version: "1.0"; epoch: string; sequence: number; captured_at: string; source_timestamp: string; received_at: string; valid_until: string;
  safety_state: "NORMAL" | "CAUTION" | "REDUCED_RISK" | "NO_NEW_RISK" | "FLATTEN_ONLY" | "HALT"; order_count: number;
  world_version: string; world_status: string;
  instruments: {instrument_id: string; quote_status: string; history_status: string; bar_count: number; regime: string; mid_price:string|null; spread_bps:string|null; return_bps:string|null; realized_volatility_bps:string|null}[];
  currency_twins:{currency:string;instrument_ids:string[];fresh_quote_count:number;macro_available:boolean}[];
};
const MAX_FUTURE_CLOCK_SKEW_MS=25;
function record(v: unknown): v is Record<string,unknown> { return v !== null && typeof v === "object" && !Array.isArray(v); }
function stamp(v: unknown): v is string {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v) && Number(v.slice(0,4)) >= 1 && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0,19) === v.slice(0,19);
}
const includes = (list: string[], value: unknown) => typeof value === "string" && list.includes(value);
function numeric(value:unknown,sign:"POSITIVE"|"NONNEGATIVE"|"SIGNED"):value is string|null {
  if(value===null) return true;
  if(typeof value!=="string" || value.length>96 || !/^-?(?:0|[1-9]\d{0,39})(?:\.\d{1,32})?(?:[Ee][+-]?\d{1,3})?$/.test(value)) return false;
  const number=Number(value),nonzero=/[1-9]/.test(value.split(/[eE]/)[0]);
  return Number.isFinite(number) && !(number===0 && nonzero) && (sign==="SIGNED" || (sign==="POSITIVE"?number>0:number>=0));
}
export function frameCurrent(frame: RealtimeFrame, now: number): boolean {
  return Number.isFinite(now) && Date.parse(frame.captured_at) <= now+MAX_FUTURE_CLOCK_SKEW_MS && now < Date.parse(frame.valid_until);
}
export function parseRealtimeFrame(value: unknown, now: number): RealtimeFrame | null {
  if (!Number.isFinite(now) || !record(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || typeof value.epoch !== "string" || !/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[0-9a-f]{12}$/.test(value.epoch) || !Number.isSafeInteger(value.sequence) || (value.sequence as number)<1 || !stamp(value.captured_at) || !stamp(value.valid_until) || !includes(["NORMAL","CAUTION","REDUCED_RISK","NO_NEW_RISK","FLATTEN_ONLY","HALT"],value.safety_state) || !Number.isSafeInteger(value.order_count) || (value.order_count as number)<0 || (value.order_count as number)>100000 || !record(value.world)) return null;
  const world=value.world;
  if(world.schema_version!=="1.0" || world.source!=="PAPER_SIMULATED" || typeof world.world_state_version!=="string" || !/^world:[a-f0-9]{64}$/.test(world.world_state_version) || !stamp(world.as_of) || Date.parse(world.as_of)!==Date.parse(value.captured_at) || !stamp(world.valid_until) || !includes(["EMPTY","READY","DEGRADED"],world.status) || !Array.isArray(world.instruments) || world.instruments.length<1 || world.instruments.length>8) return null;
  const start=Date.parse(value.captured_at),end=Date.parse(value.valid_until);
  if(end<=start || end-start>3000 || end>Date.parse(world.valid_until)) return null;
  const instruments:RealtimeFrame["instruments"]=[], seen=new Set<string>();
  for(const row of world.instruments){
    if(!record(row) || typeof row.instrument_id!=="string" || !/^[A-Z]{6}$/.test(row.instrument_id) || seen.has(row.instrument_id) || !includes(["FRESH","STALE","MISSING","UNTRUSTED","FUTURE"],row.quote_status) || !includes(["FRESH","STALE","MISSING","INSUFFICIENT"],row.history_status) || !Number.isSafeInteger(row.bar_count) || (row.bar_count as number)<0 || (row.bar_count as number)>4096 || !includes(["TRENDING","RANGING","HIGH_VOLATILITY","UNKNOWN"],row.regime)) return null;
    if(!numeric(row.mid_price,"POSITIVE") || !numeric(row.spread_bps,"NONNEGATIVE") || !numeric(row.return_bps,"SIGNED") || !numeric(row.realized_volatility_bps,"NONNEGATIVE")) return null;
    seen.add(row.instrument_id);instruments.push({instrument_id:row.instrument_id,quote_status:row.quote_status as string,history_status:row.history_status as string,bar_count:row.bar_count as number,regime:row.regime as string,mid_price:row.mid_price,spread_bps:row.spread_bps,return_bps:row.return_bps,realized_volatility_bps:row.realized_volatility_bps});
  }
  const expectedCurrencies=new Set(instruments.flatMap(row=>[row.instrument_id.slice(0,3),row.instrument_id.slice(3)]));
  if(!Array.isArray(world.currency_twins) || world.currency_twins.length!==expectedCurrencies.size || world.currency_twins.length>16) return null;
  const currency_twins:RealtimeFrame["currency_twins"]=[],currencies=new Set<string>();
  for(const row of world.currency_twins){
    if(!record(row) || typeof row.currency!=="string" || !expectedCurrencies.has(row.currency) || currencies.has(row.currency) || !Array.isArray(row.instrument_ids) || typeof row.macro_available!=="boolean" || !Number.isSafeInteger(row.fresh_quote_count)) return null;
    const ids=instruments.filter(item=>item.instrument_id.slice(0,3)===row.currency || item.instrument_id.slice(3)===row.currency).map(item=>item.instrument_id).sort();
    if(JSON.stringify(ids)!==JSON.stringify(row.instrument_ids) || row.fresh_quote_count!==instruments.filter(item=>ids.includes(item.instrument_id) && item.quote_status==="FRESH").length) return null;
    currencies.add(row.currency);currency_twins.push({currency:row.currency,instrument_ids:ids,fresh_quote_count:row.fresh_quote_count as number,macro_available:row.macro_available});
  }
  const frame:RealtimeFrame={schema_version:"1.0",epoch:value.epoch,sequence:value.sequence as number,captured_at:value.captured_at,source_timestamp:world.as_of,received_at:new Date(now).toISOString(),valid_until:value.valid_until,safety_state:value.safety_state as RealtimeFrame["safety_state"],order_count:value.order_count as number,world_version:world.world_state_version,world_status:world.status as string,instruments,currency_twins};
  return frameCurrent(frame,now)?frame:null;
}
export function acceptRealtimeFrame(previous: RealtimeFrame | null, next: RealtimeFrame, reset: boolean): boolean {
  if(previous===null) return reset;
  if(Date.parse(next.captured_at)<Date.parse(previous.captured_at)) return false;
  return next.epoch===previous.epoch ? next.sequence>previous.sequence : reset;
}

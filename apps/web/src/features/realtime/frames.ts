export type RealtimeFrame = {
  epoch: string; sequence: number; captured_at: string; valid_until: string;
  safety_state: "NORMAL" | "NO_NEW_RISK" | "FLATTEN_ONLY" | "HALT"; order_count: number;
  world_version: string; world_status: string;
  instruments: {instrument_id: string; quote_status: string; history_status: string; bar_count: number; regime: string}[];
};
function record(v: unknown): v is Record<string,unknown> { return v !== null && typeof v === "object" && !Array.isArray(v); }
function stamp(v: unknown): v is string {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v) && Number(v.slice(0,4)) >= 1 && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0,19) === v.slice(0,19);
}
const includes = (list: string[], value: unknown) => typeof value === "string" && list.includes(value);
export function frameCurrent(frame: RealtimeFrame, now: number): boolean {
  return Number.isFinite(now) && Date.parse(frame.captured_at) <= now && now < Date.parse(frame.valid_until);
}
export function parseRealtimeFrame(value: unknown, now: number): RealtimeFrame | null {
  if (!record(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || typeof value.epoch !== "string" || !/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value.epoch) || !Number.isSafeInteger(value.sequence) || (value.sequence as number)<1 || !stamp(value.captured_at) || !stamp(value.valid_until) || !includes(["NORMAL","NO_NEW_RISK","FLATTEN_ONLY","HALT"],value.safety_state) || !Number.isSafeInteger(value.order_count) || (value.order_count as number)<0 || (value.order_count as number)>100000 || !record(value.world)) return null;
  const world=value.world;
  if(world.schema_version!=="1.0" || world.source!=="PAPER_SIMULATED" || typeof world.world_state_version!=="string" || !/^world:[a-f0-9]{64}$/.test(world.world_state_version) || !stamp(world.as_of) || Date.parse(world.as_of)!==Date.parse(value.captured_at) || !stamp(world.valid_until) || !includes(["EMPTY","READY","DEGRADED"],world.status) || !Array.isArray(world.instruments) || world.instruments.length<1 || world.instruments.length>8) return null;
  const start=Date.parse(value.captured_at),end=Date.parse(value.valid_until);
  if(end<=start || end-start>3000 || end>Date.parse(world.valid_until)) return null;
  const instruments:RealtimeFrame["instruments"]=[], seen=new Set<string>();
  for(const row of world.instruments){
    if(!record(row) || typeof row.instrument_id!=="string" || !/^[A-Z]{6}$/.test(row.instrument_id) || seen.has(row.instrument_id) || !includes(["FRESH","STALE","MISSING","UNTRUSTED","FUTURE"],row.quote_status) || !includes(["FRESH","STALE","MISSING","INSUFFICIENT"],row.history_status) || !Number.isSafeInteger(row.bar_count) || (row.bar_count as number)<0 || (row.bar_count as number)>4096 || !includes(["TRENDING","RANGING","HIGH_VOLATILITY","UNKNOWN"],row.regime)) return null;
    seen.add(row.instrument_id);instruments.push({instrument_id:row.instrument_id,quote_status:row.quote_status as string,history_status:row.history_status as string,bar_count:row.bar_count as number,regime:row.regime as string});
  }
  const frame:RealtimeFrame={epoch:value.epoch,sequence:value.sequence as number,captured_at:value.captured_at,valid_until:value.valid_until,safety_state:value.safety_state as RealtimeFrame["safety_state"],order_count:value.order_count as number,world_version:world.world_state_version,world_status:world.status as string,instruments};
  return frameCurrent(frame,now)?frame:null;
}
export function acceptRealtimeFrame(previous: RealtimeFrame | null, next: RealtimeFrame, reset: boolean): boolean {
  if(previous===null) return reset;
  if(Date.parse(next.captured_at)<Date.parse(previous.captured_at)) return false;
  return next.epoch===previous.epoch ? next.sequence>previous.sequence : reset;
}

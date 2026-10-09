export type ArchivedStrategy = {strategy_ref:string;input_kind:"BAR"|"TICK";implementation_sha256:string;state:"ARCHIVED_IMPLEMENTATION";parameters:{name:string;value:number}[];source_inventory:{path:string;sha256:string}[];details_truncated:boolean;promotion_allowed:false};
export type StrategyPage = {head_hash:string;total:number;offset:number;limit:number;next_offset:number|null;strategies:ArchivedStrategy[]};
function record(value: unknown): value is Record<string,unknown> {return typeof value === "object" && value !== null && !Array.isArray(value);}
function digest(value: unknown): value is string {return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);}
function integer(value: unknown, max: number): value is number {return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= max;}
function parseStrategy(value: unknown): ArchivedStrategy | null {
  if (!record(value) || typeof value.strategy_ref !== "string" || value.strategy_ref.length>128 || !/^[a-z][a-z0-9-]{0,63}@\d+\.\d+\.\d+$/.test(value.strategy_ref) || (value.input_kind !== "BAR" && value.input_kind !== "TICK") || !digest(value.implementation_sha256) || value.state !== "ARCHIVED_IMPLEMENTATION" || value.promotion_allowed !== false || typeof value.details_truncated !== "boolean") return null;
  if (!Array.isArray(value.parameters) || value.parameters.length>16 || !Array.isArray(value.source_inventory) || value.source_inventory.length<1 || value.source_inventory.length>16) return null;
  const parameters:ArchivedStrategy["parameters"]=[],names=new Set<string>();
  for(const parameter of value.parameters) {
    if(!record(parameter) || typeof parameter.name !== "string" || !/^[a-z][a-z_]{0,31}$/.test(parameter.name) || !integer(parameter.value,4096) || names.has(parameter.name)) return null;
    names.add(parameter.name);parameters.push({name:parameter.name,value:parameter.value});
  }
  const sources:ArchivedStrategy["source_inventory"]=[];
  for(const source of value.source_inventory) {
    if(!record(source) || typeof source.path !== "string" || source.path.length<1 || source.path.length>128 || !digest(source.sha256)) return null;
    sources.push({path:source.path,sha256:source.sha256});
  }
  return {strategy_ref:value.strategy_ref,input_kind:value.input_kind,implementation_sha256:value.implementation_sha256,state:"ARCHIVED_IMPLEMENTATION",parameters,source_inventory:sources,details_truncated:value.details_truncated,promotion_allowed:false};
}
export function parseStrategies(value: unknown): StrategyPage | null {
  if(!record(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || !digest(value.head_hash) || !integer(value.total,1000) || !integer(value.offset,1000) || !integer(value.limit,10) || value.limit<1 || !Array.isArray(value.strategies) || value.strategies.length !== Math.min(value.limit,Math.max(0,value.total-value.offset))) return null;
  const next=value.offset+value.limit<value.total ? value.offset+value.limit : null;
  if(value.next_offset !== next) return null;
  const strategies:ArchivedStrategy[]=[],refs=new Set<string>();
  for(const item of value.strategies) {const row=parseStrategy(item);if(!row || refs.has(row.strategy_ref)) return null;refs.add(row.strategy_ref);strategies.push(row);}
  return {head_hash:value.head_hash,total:value.total,offset:value.offset,limit:value.limit,next_offset:next,strategies};
}

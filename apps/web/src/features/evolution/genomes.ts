export type Objective = { schema_version: "1.0"; metric: string; direction: "MAXIMIZE" | "MINIMIZE"; minimum_improvement: string | null; protocol_sha256: string };
export type Genome = { genome_ref: string; agent_id: string; agent_version: string; manifest_ref: string; parent_ref: string | null; state: "ARCHIVED_BASELINE" | "UNVERIFIED_CHALLENGER"; objective: Objective; source_inventory: {path:string;sha256:string}[]; execution_verified: false; promotion_allowed: false; details_truncated: boolean };
export type GenomePage = {head_hash:string;total:number;offset:number;limit:number;next_offset:number|null;genomes:Genome[]};
function record(value: unknown): value is Record<string,unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function text(value: unknown, max: number): value is string { return typeof value === "string" && value.length > 0 && value.length <= max; }
function digest(value: unknown): value is string { return typeof value === "string" && /^[a-f0-9]{64}$/.test(value); }
function ref(value: unknown, prefix: string): value is string { return typeof value === "string" && value.startsWith(prefix) && digest(value.slice(prefix.length)); }
function integer(value: unknown, max: number): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= max; }
const directions: Record<string,"MAXIMIZE"|"MINIMIZE"> = {CORRECTNESS:"MAXIMIZE",CALIBRATION_ERROR:"MINIMIZE",EVIDENCE_AGE_MS:"MINIMIZE",RELIABILITY:"MAXIMIZE",LATENCY_MS:"MINIMIZE",TOOL_ERROR_RATE:"MINIMIZE",COST_USD:"MINIMIZE",REGIME_CORRECTNESS:"MAXIMIZE"};
function parseGenome(value: unknown): Genome | null {
  if (!record(value) || !ref(value.genome_ref,"agent-genome:") || !text(value.agent_id,3) || !/^A(0[1-9]|1[0-8])$/.test(value.agent_id) || !text(value.agent_version,32) || !/^(0|[1-9]\d{0,5})(\.(0|[1-9]\d{0,5})){2}$/.test(value.agent_version) || !ref(value.manifest_ref,"agent-manifest:") || (value.parent_ref !== null && (!ref(value.parent_ref,"agent-genome:") || value.parent_ref === value.genome_ref))) return null;
  if (value.state !== "ARCHIVED_BASELINE" && value.state !== "UNVERIFIED_CHALLENGER") return null;
  if ((value.parent_ref === null) !== (value.state === "ARCHIVED_BASELINE") || value.execution_verified !== false || value.promotion_allowed !== false) return null;
  const objective=value.objective;
  if (!record(objective) || objective.schema_version !== "1.0" || !text(objective.metric,32) || !Object.hasOwn(directions,objective.metric) || objective.direction !== directions[objective.metric] || !digest(objective.protocol_sha256) || typeof value.details_truncated !== "boolean" || value.details_truncated !== (objective.minimum_improvement === null)) return null;
  if (objective.minimum_improvement !== null && (!text(objective.minimum_improvement,64) || !/^\d+(\.\d+)?([eE][+-]?\d+)?$/.test(objective.minimum_improvement))) return null;
  const improvement=Number(objective.minimum_improvement);
  const rate=["CORRECTNESS","CALIBRATION_ERROR","RELIABILITY","TOOL_ERROR_RATE","REGIME_CORRECTNESS"].includes(objective.metric);
  if (!Number.isFinite(improvement) || improvement < 0 || improvement > (rate ? 1 : 1000000)) return null;
  if (!Array.isArray(value.source_inventory) || value.source_inventory.length < 1 || value.source_inventory.length > 3) return null;
  const sources: Genome["source_inventory"]=[], paths=new Set<string>();
  for (const source of value.source_inventory) {
    if (!record(source) || !text(source.path,128) || !digest(source.sha256) || paths.has(source.path)) return null;
    paths.add(source.path); sources.push({path:source.path,sha256:source.sha256});
  }
  return {genome_ref:value.genome_ref,agent_id:value.agent_id,agent_version:value.agent_version,manifest_ref:value.manifest_ref,parent_ref:value.parent_ref,state:value.state,objective:{schema_version:"1.0",metric:objective.metric,direction:directions[objective.metric],minimum_improvement:objective.minimum_improvement,protocol_sha256:objective.protocol_sha256},source_inventory:sources,execution_verified:false,promotion_allowed:false,details_truncated:value.details_truncated};
}
export function parseGenomes(value: unknown): GenomePage | null {
  if (!record(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || !digest(value.head_hash) || !integer(value.total,1000) || !integer(value.offset,1000) || !integer(value.limit,10) || value.limit < 1 || !Array.isArray(value.genomes) || value.genomes.length !== Math.min(value.limit,Math.max(0,value.total-value.offset))) return null;
  const next=value.offset+value.limit < value.total ? value.offset+value.limit : null;
  if (value.next_offset !== next) return null;
  const genomes:Genome[]=[],refs=new Set<string>();
  for (const item of value.genomes) { const genome=parseGenome(item); if (!genome || refs.has(genome.genome_ref)) return null; refs.add(genome.genome_ref); genomes.push(genome); }
  return {head_hash:value.head_hash,total:value.total,offset:value.offset,limit:value.limit,next_offset:next,genomes};
}

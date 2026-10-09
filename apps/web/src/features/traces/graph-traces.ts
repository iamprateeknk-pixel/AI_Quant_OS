const states = ["CREATED", "RUNNING", "COMPLETED", "UNAVAILABLE", "EXPIRED", "DENIED", "WAIT_FOR_DATA", "INVALID", "FAILED", "TIMEOUT", "UNCERTAIN"] as const;
type Status = typeof states[number];
export type GraphRun = {run_id:string; agent_id:string; agent_version:string; task_id:string; scope:"PAPER"|"SANDBOX"; status:Status; context_ref:string; manifest_ref:string; trace_id:string; context_observed_at:string; context_valid_until:string};
export type GraphRunPage = {schema_version:"1.0"; mode:"PAPER"; archival_only:true; head_hash:string; total:number; after_run_id:string; remaining:number; next_after:string|null; records:GraphRun[]};
type TraceEvent = {sequence:number; status:Status; record_hash:string; event_hash:string; observed_at:string|null};
export type GraphTrace = GraphRun & {schema_version:"1.0"; mode:"PAPER"; archival_only:true; requires_escalation:true; input_refs:string[]; output_trace_id:string|null; output_sha256:string|null; events:TraceEvent[]; timing_state:"AVAILABLE"|"PARTIAL"|"UNAVAILABLE"|"CLOCK_REGRESSION"; elapsed_seconds:string|null};

function record(value:unknown): value is Record<string,unknown> {return typeof value === "object" && value !== null && !Array.isArray(value);}
function text(value:unknown,maximum=128): value is string {return typeof value === "string" && value.length > 0 && value.length <= maximum;}
function hash(value:unknown): value is string {return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);}
function status(value:unknown): value is Status {return typeof value === "string" && states.includes(value as Status);}
function count(value:unknown): value is number {return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 10000;}
export function validRunId(value:unknown): value is string {return typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);}

function timeMicros(value:unknown): bigint|null {
  if (typeof value !== "string" || value.startsWith("0000")) return null;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(value);
  if (!match) return null;
  const date = new Date(match[1]+"Z");
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0,19) !== match[1]) return null;
  return BigInt(date.getTime())*BigInt(1000) + BigInt((match[2]??"").padEnd(6,"0"));
}

function elapsedMicros(value:unknown): bigint|null {
  if (typeof value !== "string" || !/^(0|[1-9]\d{0,11})(?:\.\d{1,6})?$/.test(value)) return null;
  const [whole,fraction=""] = value.split(".");
  return BigInt(whole)*BigInt(1000000)+BigInt(fraction.padEnd(6,"0"));
}

function identity(value:unknown): GraphRun|null {
  if (!record(value) || !validRunId(value.run_id) || !text(value.agent_id) || !/^A(0[1-9]|1[0-8])$/.test(value.agent_id) || !text(value.agent_version,32) || !/^\d+\.\d+\.\d+$/.test(value.agent_version) || !text(value.task_id) || !status(value.status) || (value.scope !== "PAPER" && value.scope !== "SANDBOX")) return null;
  if (!text(value.context_ref) || !/^agent-context:[a-f0-9]{64}$/.test(value.context_ref) || !text(value.manifest_ref) || !/^agent-manifest:[a-f0-9]{64}$/.test(value.manifest_ref) || !text(value.trace_id) || !/^graph-trace:[a-f0-9]{64}$/.test(value.trace_id)) return null;
  const observed = timeMicros(value.context_observed_at), expiry = timeMicros(value.context_valid_until);
  if (observed === null || expiry === null || expiry <= observed) return null;
  return {run_id:value.run_id,agent_id:value.agent_id,agent_version:value.agent_version,task_id:value.task_id,scope:value.scope,status:value.status,context_ref:value.context_ref,manifest_ref:value.manifest_ref,trace_id:value.trace_id,context_observed_at:value.context_observed_at as string,context_valid_until:value.context_valid_until as string};
}

export function parseGraphRunPage(value:unknown,expectedAfter?:string,expectedHead?:string): GraphRunPage|null {
  if (!record(value) || (expectedAfter !== undefined && value.after_run_id !== expectedAfter) || (expectedHead !== undefined && value.head_hash !== expectedHead)) return null;
  if (!record(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || value.archival_only !== true || !hash(value.head_hash) || !count(value.total) || !count(value.remaining) || typeof value.after_run_id !== "string" || (value.after_run_id !== "" && !validRunId(value.after_run_id)) || !Array.isArray(value.records) || value.records.length > 25) return null;
  const records:GraphRun[]=[];
  for (const input of value.records) {
    const row = identity(input);
    if (!row || row.run_id <= value.after_run_id || (records.length && records.at(-1)!.run_id >= row.run_id)) return null;
    records.push(row);
  }
  if ((value.remaining && !records.length) || value.total < records.length+value.remaining+Number(Boolean(value.after_run_id)) || (!value.after_run_id && value.total !== records.length+value.remaining)) return null;
  const next = value.remaining ? records.at(-1)!.run_id : null;
  if (value.next_after !== next) return null;
  return {schema_version:"1.0",mode:"PAPER",archival_only:true,head_hash:value.head_hash,total:value.total,after_run_id:value.after_run_id,remaining:value.remaining,next_after:next,records};
}

export function parseGraphTrace(value:unknown,expectedRunId?:string): GraphTrace|null {
  const base = identity(value);
  if (!base || !record(value) || (expectedRunId !== undefined && base.run_id !== expectedRunId) || value.schema_version !== "1.0" || value.mode !== "PAPER" || value.archival_only !== true || value.requires_escalation !== true || !Array.isArray(value.input_refs) || value.input_refs.length > 128 || !value.input_refs.every(row=>text(row,2048)) || !Array.isArray(value.events) || value.events.length < 1 || value.events.length > 3) return null;
  if ((value.output_trace_id !== null && !text(value.output_trace_id)) || (value.output_sha256 !== null && !hash(value.output_sha256)) || ((base.status === "COMPLETED") !== (value.output_sha256 !== null)) || (base.status !== "COMPLETED" && value.output_trace_id !== null)) return null;
  const events:TraceEvent[]=[];
  for (const raw of value.events) {
    if (!record(raw) || typeof raw.sequence !== "number" || !Number.isSafeInteger(raw.sequence) || raw.sequence < 1 || !status(raw.status) || !hash(raw.record_hash) || !hash(raw.event_hash) || (raw.observed_at !== null && timeMicros(raw.observed_at) === null) || (events.length && events.at(-1)!.sequence >= raw.sequence)) return null;
    events.push({sequence:raw.sequence,status:raw.status,record_hash:raw.record_hash,event_hash:raw.event_hash,observed_at:raw.observed_at as string|null});
  }
  if (events[0].status !== "CREATED" || events.at(-1)!.status !== base.status || (events.length === 2 && ["CREATED","COMPLETED","UNCERTAIN"].includes(events[1].status)) || (events.length === 3 && (events[1].status !== "RUNNING" || ["CREATED","RUNNING"].includes(events[2].status)))) return null;
  const times = events.map(row=>row.observed_at === null ? null : timeMicros(row.observed_at)!);
  const state = times.every(row=>row === null) ? "UNAVAILABLE" : times.some(row=>row === null) ? "PARTIAL" : times.some((row,index)=>index > 0 && row! < times[index-1]!) ? "CLOCK_REGRESSION" : "AVAILABLE";
  if (value.timing_state !== state) return null;
  const expectedElapsed = state === "AVAILABLE" && events.length === 3 ? times[2]! - times[1]! : null;
  if (expectedElapsed === null ? value.elapsed_seconds !== null : elapsedMicros(value.elapsed_seconds) !== expectedElapsed) return null;
  return {...base,schema_version:"1.0",mode:"PAPER",archival_only:true,requires_escalation:true,input_refs:value.input_refs as string[],output_trace_id:value.output_trace_id as string|null,output_sha256:value.output_sha256 as string|null,events,timing_state:state,elapsed_seconds:value.elapsed_seconds as string|null};
}

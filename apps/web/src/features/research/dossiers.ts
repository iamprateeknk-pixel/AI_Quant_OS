export type Gate = { name: string; state: "PASS" | "FAIL" | "PENDING"; reason: string };
export type Dossier = { candidate_id: string; experiment_id: string; hypothesis_id: string; hypothesis: string; state: "CHALLENGER" | "FAILED" | "REJECTED" | "WITHDRAWN"; outcome: "FAILED" | "REJECTED" | "VALIDATION_INCOMPLETE"; revision: number; report_sha256: string; plan_sha256: string; last_event_sha256: string; strategy_ref: string | null; fold_count: number; attempt_count: number; gates: Gate[]; reasons: string[]; details_truncated: boolean; promotion_allowed: false };
export type DossierPage = { head_hash: string; total: number; offset: number; limit: number; next_offset: number | null; candidates: Dossier[] };
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function text(value: unknown, maximum: number): value is string { return typeof value === "string" && value.length <= maximum; }
function digest(value: unknown): value is string { return typeof value === "string" && /^[a-f0-9]{64}$/.test(value); }
function integer(value: unknown, maximum: number): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= maximum; }

function parseDossier(value: unknown): Dossier | null {
  if (!record(value) || !digest(value.candidate_id) || !digest(value.report_sha256) || !digest(value.plan_sha256) || !digest(value.last_event_sha256) || !text(value.experiment_id,64) || !/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(value.experiment_id) || !text(value.hypothesis_id,64) || !/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(value.hypothesis_id) || !text(value.hypothesis,1024)) return null;
  const state=value.state, outcome=value.outcome;
  if (state !== "CHALLENGER" && state !== "FAILED" && state !== "REJECTED" && state !== "WITHDRAWN") return null;
  if (outcome !== "FAILED" && outcome !== "REJECTED" && outcome !== "VALIDATION_INCOMPLETE") return null;
  if ((state === "FAILED" || state === "REJECTED") ? outcome !== state : outcome !== "VALIDATION_INCOMPLETE") return null;
  if (!integer(value.revision,100000) || value.revision < 1 || !integer(value.fold_count,1000) || !integer(value.attempt_count,128) || (value.strategy_ref !== null && !text(value.strategy_ref,128)) || value.promotion_allowed !== false || typeof value.details_truncated !== "boolean") return null;
  if (!Array.isArray(value.gates) || value.gates.length > 16 || !Array.isArray(value.reasons) || value.reasons.length > 16 || !value.reasons.every((reason) => text(reason,256))) return null;
  const gates: Gate[]=[];
  for (const gate of value.gates) {
    if (!record(gate) || !text(gate.name,64) || !text(gate.reason,256) || (gate.state !== "PASS" && gate.state !== "FAIL" && gate.state !== "PENDING")) return null;
    gates.push({name:gate.name,state:gate.state,reason:gate.reason});
  }
  return { candidate_id:value.candidate_id, experiment_id:value.experiment_id, hypothesis_id:value.hypothesis_id, hypothesis:value.hypothesis, state, outcome, revision:value.revision, report_sha256:value.report_sha256,plan_sha256:value.plan_sha256,last_event_sha256:value.last_event_sha256,strategy_ref:value.strategy_ref,fold_count:value.fold_count,attempt_count:value.attempt_count,gates,reasons:[...value.reasons],details_truncated:value.details_truncated,promotion_allowed:false };
}

export function parseDossiers(value: unknown): DossierPage | null {
  if (!record(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || !digest(value.head_hash) || !integer(value.total,1000) || !integer(value.offset,1000) || !integer(value.limit,2) || value.limit < 1 || !Array.isArray(value.candidates) || value.candidates.length !== Math.min(value.limit,Math.max(0,value.total-value.offset))) return null;
  const next=value.offset+value.limit < value.total ? value.offset+value.limit : null;
  if (value.next_offset !== next) return null;
  const candidates: Dossier[]=[]; const ids=new Set<string>();
  for (const item of value.candidates) { const row=parseDossier(item); if (!row || ids.has(row.candidate_id)) return null; ids.add(row.candidate_id); candidates.push(row); }
  return {head_hash:value.head_hash,total:value.total,offset:value.offset,limit:value.limit,next_offset:next,candidates};
}

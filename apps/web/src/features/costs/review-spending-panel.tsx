import {parseReviewSpending} from "./review-spending";
import Link from "next/link";
import {SpendingTables} from "./spending-tables";

export function ReviewSpendingPanel({result}:{result:{ok:true;value:unknown}|{ok:false;reason:string}}){
 const budget=result.ok?parseReviewSpending(result.value):null,available=budget?.state==="ENFORCED"||budget?.state==="BREACHED",signIn=!result.ok&&result.reason==="SIGN_IN";
 return <section className="panel content-panel">
  <div className="panel-heading"><h2>OpenAI review spending capacity</h2><span role="status">{budget?.state??"UNAVAILABLE"}</span></div>
  <p className="panel-muted">Committed capacity combines reported usage and full unsettled reservations at configured rate ceilings. This is not a provider invoice. Other model routes, compute and data charges are not measured here.</p>
  {available&&budget?<>
   {budget.state==="BREACHED"&&<p className="notice warning" role="status">Generation blocked: a usage report exceeded its reserved token bound. Verified ledger evidence is retained; no reset or refund is inferred.</p>}
   <dl className="fact-list"><div><dt>Snapshot read (UTC)</dt><dd><time dateTime={budget.observed_at}>{budget.observed_at}</time></dd></div><div><dt>Model</dt><dd>{budget.model}</dd></div><div><dt>Periods (UTC)</dt><dd>{budget.day} / {budget.month}</dd></div><div><dt>Recovery spending</dt><dd>Disabled; protected reserves cannot fund routine review</dd></div></dl>
   <p className="panel-muted">Unsettled requests may still be running or may have ended without a verified usage report. Their full holds survive restart and carry into later periods until settled. These rows describe this snapshot; refresh to read later changes.</p>
   <SpendingTables agents={budget.agents} />
   <details className="research-details"><summary>Inspect policy and journal fingerprints</summary><dl className="fact-list research-provenance"><div><dt>Policy fingerprint</dt><dd className="mono">{budget.policy_sha256}</dd></div><div><dt>Journal head</dt><dd className="mono">{budget.head_hash}</dd></div></dl></details>
  </>:<div className="empty-list" role="status">{signIn?"Sign in to view protected review spending capacity.":budget?.state==="NOT_CONFIGURED"?"Review spending limits are not configured. No usage or remaining capacity is inferred.":"Verified review spending capacity is unavailable. No usage or remaining capacity is inferred."}</div>}
  <div className="health-navigation">{signIn?<Link className="health-link" href="/risk">Sign in at Risk Center</Link>:<form method="get" action="/costs"><button type="submit" className="health-link">Refresh spending snapshot</button></form>}</div>
 </section>;
}

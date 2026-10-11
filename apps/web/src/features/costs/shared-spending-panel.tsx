import Link from "next/link";
import {parseSharedSpending} from "./shared-spending";
import {SpendingTables} from "./spending-tables";
import {ReviewSpendingPanel} from "./review-spending-panel";

type Result={ok:true;value:unknown}|{ok:false;reason:string};
const messages={NOT_CONFIGURED:"Shared spending limits are not configured. No combined usage or remaining capacity is inferred.",NOT_SHARED:"Configured spending uses a standalone ledger. Its review budget is shown separately when available.",SEPARATE_LEDGERS:"Providers use separate ledgers. Combined usage and remaining capacity are withheld.",INCOMPLETE_CONFIGURATION:"Some configured providers have no spending ledger. Complete shared capacity is withheld.",UNAVAILABLE:"Verified shared spending capacity is unavailable. No combined usage or remaining capacity is inferred."};
export function SharedSpendingPanel({result}:{result:Result}){
 const budget=result.ok?parseSharedSpending(result.value):null,available=budget?.state==="ENFORCED"||budget?.state==="BREACHED",signIn=!result.ok&&result.reason==="SIGN_IN";
 return <section className="panel content-panel">
  <div className="panel-heading"><h2>Shared provider spending capacity</h2><span role="status">{budget?.state??"UNAVAILABLE"}</span></div>
  <p className="panel-muted">This is one original ledger snapshot. Aggregate role capacity is shown once; each model retains its own limits and counted-token rules. Declared rate capacity is not a provider invoice. Compute, data and other ledgers are not included.</p>
  {available&&budget?<>
   {budget.state==="BREACHED"&&<p className="notice warning" role="status">Generation blocked across this shared ledger: a native usage report exceeded its reserved bound. Original evidence is retained.</p>}
   <dl className="fact-list"><div><dt>Snapshot read (UTC)</dt><dd><time dateTime={budget.observed_at}>{budget.observed_at}</time></dd></div><div><dt>Periods (UTC)</dt><dd>{budget.day} / {budget.month}</dd></div><div><dt>Configured spending routes</dt><dd>{budget.routes.map(route=>`${route.purpose} · ${route.model} · ${route.agent_id}@${route.agent_version}`).join("; ")}</dd></div><div><dt>Recovery spending</dt><dd>Disabled; protected reserves cannot fund routine requests</dd></div></dl>
   <p className="panel-muted">Counted tokens add each model’s accounting units: Jev input tokens only, OpenAI input and output tokens. Unsettled requests retain their full reservations across restart and UTC periods. This snapshot does not update automatically; refresh for a later observation.</p>
   <SpendingTables agents={budget.agents} tokenCaption="Counted tokens: aggregate role capacity" moneyCaption="Declared USD capacity: aggregate roles, exact nanodollar precision" />
   {budget.models.map(model=><details className="research-details" key={model.model}><summary>{model.model} · {model.provider_id}</summary><p className="panel-muted">{model.provider_id==="JEV"?"Input tokens only; reported output tokens are free.":"Input and output tokens."} Model capacity and aggregate capacity must both admit each request. These model rows are already included above.</p><SpendingTables agents={model.agents} tokenCaption={`${model.model}: counted tokens and model limits`} moneyCaption={`${model.model}: declared USD capacity`} /><dl className="fact-list research-provenance"><div><dt>Model policy fingerprint</dt><dd className="mono">{model.policy_sha256}</dd></div></dl></details>)}
   <details className="research-details"><summary>Inspect original ledger identity</summary><dl className="fact-list research-provenance"><div><dt>Book identity</dt><dd className="mono">{budget.book_id}</dd></div><div><dt>Shared policy fingerprint</dt><dd className="mono">{budget.policy_sha256}</dd></div><div><dt>Journal head</dt><dd className="mono">{budget.head_hash}</dd></div></dl></details>
  </>:<div className="empty-list" role="status">{signIn?"Sign in to view protected shared spending capacity.":messages[budget?.state as keyof typeof messages]??messages.UNAVAILABLE}</div>}
  <div className="health-navigation">{signIn?<Link className="health-link" href="/risk">Sign in at Risk Center</Link>:<form method="get" action="/costs"><button className="health-link" type="submit">Refresh shared spending snapshot</button></form>}</div>
 </section>;
}
export function SpendingPanels({sharedResult,reviewResult}:{sharedResult:Result;reviewResult:Result}){
 const shared=sharedResult.ok?parseSharedSpending(sharedResult.value):null;
 return <><SharedSpendingPanel result={sharedResult} />{shared?.state!=="ENFORCED"&&shared?.state!=="BREACHED"&&<ReviewSpendingPanel result={reviewResult} />}</>;
}

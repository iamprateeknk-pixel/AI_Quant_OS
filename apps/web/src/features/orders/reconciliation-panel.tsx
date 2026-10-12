"use client";

import {useState, useTransition, type FormEvent} from "react";
import {useRouter} from "next/navigation";
import {parsePaperReconciliation, type PaperReconciliation} from "./reconciliation";

function record(value:unknown):value is Record<string,unknown>{return typeof value==="object"&&value!==null&&!Array.isArray(value);}

export function PaperReconciliationPanel(){
  const router=useRouter();
  const [confirmed,setConfirmed]=useState(false);
  const [pending,startTransition]=useTransition();
  const [result,setResult]=useState<PaperReconciliation|null>(null);
  const [message,setMessage]=useState("");

  function submit(event:FormEvent<HTMLFormElement>){
    event.preventDefault();
    if(!confirmed||pending)return;
    setResult(null);setMessage("");
    startTransition(async()=>{
      try{
        const response=await fetch("/api/orders/reconcile",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirmation:"COMPARE"}),signal:AbortSignal.timeout(10000)});
        const payload:unknown=await response.json();
        if(!response.ok){setMessage(record(payload)&&typeof payload.detail==="string"?payload.detail:"Paper ledger comparison failed. Check Risk Center and the audit record before repeating it.");return;}
        const parsed=parsePaperReconciliation(payload);
        if(!parsed){setMessage("The comparison receipt could not be verified. Check Risk Center and the audit record before repeating it.");return;}
        setResult(parsed);
        setMessage(parsed.matches?"Paper fill ledgers match. The comparison was recorded in the audit log.":"Paper fill ledgers differ. The backend escalated safety to NO_NEW_RISK; review Risk Center before any further paper orders.");
        router.refresh();
      }catch{setMessage("The comparison outcome is unknown. Check Risk Center and the audit record before repeating it.");}
      finally{setConfirmed(false);}
    });
  }

  return <section className="panel content-panel paper-reconciliation" aria-labelledby="paper-reconciliation-heading">
    <div className="panel-heading"><h2 id="paper-reconciliation-heading">Paper fill ledger reconciliation</h2><span>OPERATOR ACTION</span></div>
    <p className="panel-muted">Compare local paper fills with the paper simulator's fill ledger. The comparison is audited; a mismatch can raise the safety state to NO_NEW_RISK. This does not connect to an external broker.</p>
    <form onSubmit={submit}>
      <label className="reconciliation-confirm"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)} /> I understand a mismatch may stop new paper risk and will review the resulting receipt.</label>
      <button type="submit" disabled={!confirmed||pending}>{pending?"Comparing paper ledgers…":"Compare paper ledgers"}</button>
    </form>
    {message&&<p className="reconciliation-message" role="status" aria-live="polite">{message}{message.startsWith("Sign in")&&<> <a href="/risk">Open Risk Center</a></>}</p>}
    {result&&<>
      <dl className="fact-list"><div><dt>EURUSD net quantity · local / simulator</dt><dd>{result.local_net_quantity} / {result.broker_net_quantity}</dd></div><div><dt>EURUSD cash flow · local / simulator</dt><dd>{result.local_cash_flow_quote} / {result.broker_cash_flow_quote}</dd></div><div><dt>Ledger result</dt><dd>{result.matches?"Match":"Mismatch"}</dd></div></dl>
      {result.instruments.length>0&&<div className="table-scroll"><table><caption className="sr-only">Per-instrument paper fill ledger reconciliation</caption><thead><tr><th scope="col">Instrument</th><th scope="col">Result</th><th scope="col">Net quantity · local / simulator</th><th scope="col">Cash flow · local / simulator</th></tr></thead><tbody>{result.instruments.map(row=><tr key={row.instrument_id}><th scope="row">{row.instrument_id}</th><td>{row.matches?"Match":"Mismatch"}</td><td>{row.local_net_quantity} / {row.broker_net_quantity}</td><td>{row.local_cash_flow_quote} / {row.broker_cash_flow_quote}</td></tr>)}</tbody></table></div>}
      {(result.local_only_fill_ids.length>0||result.broker_only_fill_ids.length>0||result.conflicting_fill_ids.length>0)&&<details className="health-record"><summary>Inspect differing fill references</summary><dl className="fact-list">{result.local_only_fill_ids.length>0&&<div><dt>Local-only fills</dt><dd className="mono">{result.local_only_fill_ids.join(", ")}</dd></div>}{result.broker_only_fill_ids.length>0&&<div><dt>Simulator-only fills</dt><dd className="mono">{result.broker_only_fill_ids.join(", ")}</dd></div>}{result.conflicting_fill_ids.length>0&&<div><dt>Conflicting fill IDs</dt><dd className="mono">{result.conflicting_fill_ids.join(", ")}</dd></div>}</dl></details>}
    </>}
  </section>;
}

"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import type { PublicSession } from "@/features/operator-session/boundary";

type Command = { action_id:string; requested_enabled:boolean; expected_enabled:boolean; reason:string; operator_session_id:string; client_timestamp:string; confirmation:"ENABLE"|"DISABLE" };
type Receipt = { decision:"ACCEPTED"|"REJECTED"; authoritative_enabled:boolean; rejection_reason:string|null; action_id:string; server_timestamp:string };
function receipt(value:unknown): Receipt|null {
  if(typeof value!=="object"||value===null||Array.isArray(value))return null;
  const item=value as Record<string,unknown>;
  return (item.decision==="ACCEPTED"||item.decision==="REJECTED")&&typeof item.authoritative_enabled==="boolean"&&typeof item.action_id==="string"&&typeof item.server_timestamp==="string"&&(item.rejection_reason===null||typeof item.rejection_reason==="string")?item as Receipt:null;
}
async function readReceipt(actionId:string) {
  const response=await fetch(`/api/brokers/route-controls?action_id=${encodeURIComponent(actionId)}`,{method:"GET",cache:"no-store",signal:AbortSignal.timeout(6000)});
  return readResponse(response);
}
async function submitCommand(body:unknown) {
  const response=await fetch("/api/brokers/route-controls",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:AbortSignal.timeout(6000)});
  return readResponse(response);
}
async function readResponse(response:Response) {
  const value=await response.json();
  if(!response.ok)throw new Error(typeof value.detail==="string"?value.detail:"Broker route service unavailable");
  return value;
}

export function BrokerRouteControlPanel({enabled,initialSession}:{enabled:boolean;initialSession:PublicSession|null}) {
  const [session]=useState(initialSession);
  const [active,setActive]=useState(enabled);
  const [reason,setReason]=useState("");
  const [confirmation,setConfirmation]=useState("");
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  const [result,setResult]=useState<Receipt|null>(null);
  const [pending,setPending]=useState<Command|null>(()=>{if(typeof window==="undefined")return null;try{const item=JSON.parse(sessionStorage.getItem("paper-route-control")??"null");return item&&item.operator_session_id===initialSession?.session_id?item:null;}catch{return null;}});
  const requested=!active;
  async function submit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault(); if(!session||busy)return;
    const form=event.currentTarget,credential=String(new FormData(form).get("reauth")??"");
    const input=form.elements.namedItem("reauth");if(input instanceof HTMLInputElement)input.value="";
    const command=pending??{action_id:crypto.randomUUID(),requested_enabled:requested,expected_enabled:active,reason,operator_session_id:session.session_id,client_timestamp:new Date().toISOString(),confirmation:requested?"ENABLE":"DISABLE" as const};
    if(!pending){try{sessionStorage.setItem("paper-route-control",JSON.stringify(command));}catch{setMessage("Cannot retain the action identity in this browser. No command was sent.");return;}}
    setBusy(true);setMessage("Waiting for the audited broker route receipt.");
    try{const value=receipt(await submitCommand({...command,credential}));if(!value)throw new Error("The backend returned an invalid route receipt.");setResult(value);setActive(value.authoritative_enabled);setPending(null);sessionStorage.removeItem("paper-route-control");setReason("");setConfirmation("");setMessage(value.decision==="ACCEPTED"?"Broker route state updated from the backend receipt.":`Route change rejected: ${value.rejection_reason??"policy"}.`);}
    catch(error){setMessage(error instanceof Error?error.message:"Outcome unknown. Check the receipt before retrying.");}
    finally{setBusy(false);}
  }
  async function checkReceipt(){if(!pending)return;setBusy(true);try{const found=receipt(await readReceipt(pending.action_id));if(!found)throw new Error("No committed receipt found yet.");setResult(found);setActive(found.authoritative_enabled);setPending(null);sessionStorage.removeItem("paper-route-control");setMessage("Receipt checked.");}catch(error){setMessage(error instanceof Error?error.message:"Receipt unavailable.");}finally{setBusy(false);}}
  const confirmed=confirmation===(requested?"ENABLE":"DISABLE");
  return <section className="panel content-panel"><div className="panel-heading"><h2>Paper new order route</h2><span>{active?"ENABLED":"DISABLED"}</span></div>
    <p>Controls admission of new simulated PAPER orders. Existing orders and positions continue through their normal lookup and risk handling.</p>
    {!session?<p className="panel-muted">Sign in at <Link href="/risk">Risk Center</Link> to change the paper route.</p>:<form className="operator-control-form" onSubmit={submit}>
      <label htmlFor="paper-route-reason">Reason for route change</label><textarea id="paper-route-reason" value={pending?.reason??reason} onChange={e=>setReason(e.target.value)} required maxLength={512} disabled={busy||!!pending}/>
      <label htmlFor="paper-route-confirm">Type {requested?"ENABLE":"DISABLE"} to confirm</label><input id="paper-route-confirm" value={pending?.confirmation??confirmation} onChange={e=>setConfirmation(e.target.value)} autoComplete="off" disabled={busy||!!pending}/>
      <label htmlFor="paper-route-reauth">Re-enter access code</label><input id="paper-route-reauth" name="reauth" type="password" autoComplete="off" required maxLength={512} disabled={busy}/>
      <p className="panel-muted">Current expected route state: {active?"Enabled":"Disabled"}. The command affects PAPER new orders only.</p>
      <div className="operator-buttons"><button type="submit" disabled={busy||!reason.trim()&&!pending||(!confirmed&&!pending)}>{pending?"Retry same route action":requested?"Enable new PAPER orders":"Disable new PAPER orders"}</button>{pending&&<button type="button" disabled={busy} onClick={checkReceipt}>Check receipt</button>}</div>
    </form>}
    <div role="status" aria-live="polite" className="operator-message">{message}</div>
    {result&&<p className="panel-muted">Receipt {result.action_id} · {new Date(result.server_timestamp).toLocaleString()} · <Link href="/audit">View audit trail</Link></p>}
  </section>;
}

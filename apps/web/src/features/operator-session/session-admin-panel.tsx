"use client";

import { useEffect, useState, useTransition, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { publicSession, publicSessionInventory, publicSessionRevocation, record, type SessionInventory } from "./boundary";
import type { ProtectedRead } from "./server";

function inventory(result: ProtectedRead): SessionInventory | null {
  if (!result.ok) return null;
  try { return publicSessionInventory(result.value); } catch { return null; }
}
async function command(path: string, body: unknown): Promise<unknown> {
  const response = await fetch(path,{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:AbortSignal.timeout(8000)});
  const value = record(await response.json());
  if(!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Request could not be confirmed. Refresh before trying again.");
  return value;
}
function utc(value: string) { return new Date(value).toISOString().replace("T"," ").replace(/\.\d{3}Z$/," UTC"); }

export function SessionAdminPanel({result}:{result:ProtectedRead}) {
  const router=useRouter(), view=inventory(result);
  const [selected,setSelected]=useState<string|null>(null),[busy,setBusy]=useState(false),[message,setMessage]=useState("");
  const [expired,setExpired]=useState(false),[refreshing,refresh]=useTransition();
  const currentExpiry=view?.sessions.find(row=>row.session_id===view.current_session_id)?.expires_at;
  const observed=view?.observed_at;
  useEffect(()=>{
    if(!currentExpiry||!observed) return;
    const timer=setTimeout(()=>setExpired(true),Math.max(0,Date.parse(currentExpiry)-Date.parse(observed)));
    return ()=>clearTimeout(timer);
  },[currentExpiry,observed]);
  const target=view?.sessions.find(row=>row.session_id===selected);
  const disabled=busy||refreshing||expired;
  function reload(){setSelected(null);refresh(()=>router.refresh());}
  async function signIn(event:FormEvent<HTMLFormElement>){
    event.preventDefault();const form=event.currentTarget,credential=String(new FormData(form).get("credential")??"");form.reset();setBusy(true);setMessage("");
    try{publicSession(await command("/api/operator/session",{credential}));setExpired(false);reload();}
    catch(error){setMessage(error instanceof Error?error.message:"Sign-in could not be confirmed.");}
    finally{setBusy(false);}
  }
  async function revoke(event:FormEvent<HTMLFormElement>){
    event.preventDefault();if(!view||!target||disabled)return;
    const form=event.currentTarget,data=new FormData(form),credential=String(data.get("credential")??"");
    if(data.get("confirm")!=="yes")return;
    form.reset();setBusy(true);setMessage("Awaiting confirmation. The session has not been confirmed revoked.");
    try{
      const raw=await command("/api/operator/sessions/revoke",{credential,target_session_id:target.session_id,expected_head:view.source_head});
      const receipt=publicSessionRevocation(raw,target.session_id,view.current_session_id);
      if(receipt.source_head===view.source_head)throw new Error("Revocation could not be verified. Refresh the list.");
      setMessage(receipt.current_session_revoked?"This session was revoked. You are signed out.":"Selected session revoked.");reload();
    }catch(error){setMessage(error instanceof Error?error.message:"Outcome unknown. Refresh the session list before trying again.");}
    finally{setBusy(false);}
  }
  const signInRequired=(!result.ok&&result.reason==="SIGN_IN")||expired;
  return <section className="panel content-panel operator-console session-admin" aria-labelledby="owner-sessions-heading">
    <div className="panel-heading"><h2 id="owner-sessions-heading">Owner sessions</h2><span>LOCAL OWNER</span></div>
    <p className="panel-muted">Sessions expire after 15 minutes. Ending a session removes its access to protected views and controls. It does not cancel an action already admitted by the server.</p>
    {signInRequired?<form className="operator-login" onSubmit={signIn}><label htmlFor="session-owner-access">Operator access code</label><input id="session-owner-access" name="credential" type="password" autoComplete="off" maxLength={512} required disabled={busy}/><button type="submit" disabled={busy}>{busy?"Signing in…":"Sign in"}</button></form>:!view?<div className="empty-list">Session inventory could not be verified. Refresh to check the owner session service.</div>:<>
      <div className="operator-session-status"><span>{view.sessions.length} retained {view.sessions.length===1?"session":"sessions"} · Observed {utc(view.observed_at)}</span><button type="button" onClick={reload} disabled={busy||refreshing}>Refresh sessions</button></div>
      <div className="table-scroll"><table><thead><tr><th>Session</th><th>Status</th><th>Created, UTC</th><th>Expires, UTC</th><th>Action</th></tr></thead><tbody>{view.sessions.map(row=><tr key={row.session_id}><td className="mono">{row.session_id}</td><td>{row.status==="CURRENT"?"This session":row.status==="ACTIVE"?"Active":row.status==="EXPIRED"?"Expired":"Not yet valid"}</td><td>{utc(row.created_at)}</td><td>{utc(row.expires_at)}</td><td><button type="button" aria-pressed={selected===row.session_id} aria-label={`Select session ${row.session_id} for revocation`} disabled={disabled||row.status==="FUTURE"} onClick={()=>{setSelected(row.session_id);setMessage("");}}>Select</button></td></tr>)}</tbody></table></div>
      {target&&<form className="operator-control-form" onSubmit={revoke}><fieldset disabled={disabled}><legend>Revoke selected session</legend><p className="mono">{target.session_id}</p><p>{target.session_id===view.current_session_id?"This is your current session. Revoking it signs you out immediately; sign in again to continue.":"This session will lose access immediately. Its owner can sign in again with the access code."}</p><label className="session-confirm"><input type="checkbox" name="confirm" value="yes" required/>Revoke only this selected session</label><label htmlFor="session-revoke-access">Re-enter access code</label><input id="session-revoke-access" name="credential" type="password" autoComplete="off" maxLength={512} required/><div className="operator-buttons"><button type="submit">{busy?"Awaiting confirmation…":"Revoke session"}</button><button type="button" onClick={()=>setSelected(null)}>Cancel</button></div></fieldset></form>}
    </>}
    <div role="status" aria-live="polite" className="operator-message">{refreshing?"Refreshing the session list…":message}</div>
    {!view&&!signInRequired&&<div className="operator-session-status"><button type="button" onClick={reload} disabled={busy||refreshing}>Refresh sessions</button></div>}
  </section>;
}

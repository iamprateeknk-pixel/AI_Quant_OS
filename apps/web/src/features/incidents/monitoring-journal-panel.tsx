"use client";

import Link from "next/link";
import {useCallback,useEffect,useRef,useState} from "react";
import {hasOperatorSession} from "@/features/operator-session/browser";
import {parseMonitoringAcknowledgement,parseMonitoringJournal,type MonitoringEpisode,type MonitoringJournalPage} from "./monitoring-journal";

type View={status:"LOADING"}|{status:"READY";page:MonitoringJournalPage}|{status:"UNAVAILABLE";reason:"SIGN_IN"|"CHANGED"|"UNAVAILABLE"};
const label={PENDING:"Pending",FIRING:"Firing",NOT_CURRENTLY_OBSERVED:"Not currently observed",OBSERVED_INACTIVE:"Observed inactive",OBSERVED_REPLACED:"Replaced by a newer source episode"};

async function decoded(response:Response):Promise<unknown> {
 if(!response.ok){await response.body?.cancel();throw new Error(String(response.status));}
 const reader=response.body?.getReader();if(!reader)throw new Error("History unavailable");
 const chunks:Uint8Array[]=[];let length=0;
 try{for(;;){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>262144)throw new Error("History exceeded its limit");chunks.push(value);}}
 finally{await reader.cancel();}
 const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
 return JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));
}

export function MonitoringJournalPanel() {
 const [view,setView]=useState<View>({status:"LOADING"});
 const [pending,setPending]=useState(false);
 const [message,setMessage]=useState("");
 const requestSequence=useRef(0);
 const load=useCallback(async(offset=0,head?:string,signal?:AbortSignal)=>{
  const sequence=++requestSequence.current;
  try{
   if(!await hasOperatorSession(signal))throw new Error("401");
   const query=offset?`?offset=${offset}&head=${head}`:"";
   const response=await fetch(`/api/incidents/journal${query}`,{cache:"no-store",redirect:"error",credentials:"same-origin",signal});
   const value=parseMonitoringJournal(await decoded(response));
   if(!value)throw new Error("INVALID");
   if(sequence===requestSequence.current)setView({status:"READY",page:value});
  }catch(error){
   if(signal?.aborted||sequence!==requestSequence.current)return;
   const reason=error instanceof Error&&error.message==="401"?"SIGN_IN":error instanceof Error&&error.message==="409"?"CHANGED":"UNAVAILABLE";
   setView({status:"UNAVAILABLE",reason});
  }
 },[]);
 useEffect(()=>{
  const controller=new AbortController();
  queueMicrotask(()=>{if(!controller.signal.aborted)void load(0,undefined,controller.signal);});
  return ()=>{controller.abort();};
 },[load]);

 const acknowledge=useCallback(async(episode:MonitoringEpisode)=>{
  if(view.status!=="READY"||pending)return;
  setPending(true);setMessage("");
  const actionId=crypto.randomUUID();
  try{
   if(!await hasOperatorSession())throw new Error("401");
   const response=await fetch("/api/incidents/journal/acknowledge",{
    method:"POST",cache:"no-store",redirect:"error",credentials:"same-origin",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({episode_id:episode.episode_id,action_id:actionId,expected_head:view.page.head_hash}),
   });
   const receipt=parseMonitoringAcknowledgement(await decoded(response));
   if(!receipt||receipt.action_id!==actionId||receipt.episode_id!==episode.episode_id||receipt.source_head!==view.page.head_hash)throw new Error("OUTCOME_UNKNOWN");
   setMessage("Review receipt committed. This does not clear the source episode or change paper safety controls.");
   await load(0);
  }catch(error){
   const status=error instanceof Error?error.message:"";
   setMessage(status==="401"?"Sign in again to record a review.":status==="409"?"History changed. Refreshing the current journal page.":"Review outcome is uncertain. Refresh history before trying again.");
   await load(0);
  }finally{setPending(false);}
 },[load,pending,view]);

 return <section className="panel content-panel" aria-labelledby="monitoring-journal-heading">
  <div className="panel-heading"><h2 id="monitoring-journal-heading">Monitoring incident history</h2><span>LOCAL AUDIT JOURNAL</span></div>
  <p className="panel-muted">History records selected local source transitions. A review receipt documents operator review only; it does not resolve an episode, verify notification delivery, or change risk and trading controls.</p>
  {view.status==="LOADING"&&<p role="status">Loading the protected incident journal.</p>}
  {view.status==="UNAVAILABLE"&&<div className="empty-list" role="status">{view.reason==="SIGN_IN"?"Sign in to read or acknowledge monitoring history.":view.reason==="CHANGED"?"The journal changed while paging. Refresh from the first page.":"The protected incident journal is unavailable. No history state is inferred."}</div>}
  {view.status==="READY"&&view.page.episodes.length===0&&<div className="empty-list" role="status">No episode transitions are recorded on this page. This does not establish a healthy service.</div>}
  {view.status==="READY"&&view.page.episodes.length>0&&<div className="table-scroll"><table>
   <caption>Original monitoring episodes, ordered by first observation</caption>
   <thead><tr><th scope="col">Rule</th><th scope="col">Recorded state</th><th scope="col">First observed, UTC</th><th scope="col">Active since, UTC</th><th scope="col">Review</th><th scope="col">Runbook</th></tr></thead>
   <tbody>{view.page.episodes.map(episode=><tr key={episode.episode_id}>
    <th scope="row">{episode.rule_name==="PaperHttpScrapeFailed"?"Metrics scrape failed":episode.rule_name==="PaperHttpScrapeMissing"?"Metrics observation missing":"Rule evaluation source unavailable"}</th>
    <td>{label[episode.state]}</td>
    <td><time dateTime={episode.first_observed_at}>{episode.first_observed_at}</time></td>
    <td><time dateTime={episode.active_since}>{episode.active_since}</time></td>
    <td>{episode.acknowledgement?<span>Reviewed <time dateTime={episode.acknowledgement.acknowledged_at}>{episode.acknowledgement.acknowledged_at}</time>; control action not authorized.</span>:<button type="button" disabled={pending} aria-label={`Acknowledge review for ${episode.rule_name}`} onClick={()=>void acknowledge(episode)}>{pending?"Saving review…":"Acknowledge review"}</button>}</td>
    <td><Link className="health-link" href={`/incidents/runbook#${episode.rule_name==="PaperHttpScrapeFailed"?"scrape-failed":episode.rule_name==="PaperHttpScrapeMissing"?"scrape-missing":"source-unavailable"}`} prefetch={false}>Open runbook</Link></td>
   </tr>)}</tbody>
  </table></div>}
  {message&&<p role="status">{message}</p>}
  {view.status==="READY"&&<div className="health-navigation">
   <button className="health-link" type="button" disabled={pending} onClick={()=>{setMessage("");void load(0);}}>Refresh history</button>
   <span>Showing {view.page.total===0?0:view.page.offset+1}–{view.page.offset+view.page.episodes.length} of {view.page.total}</span>
   <button className="health-link" type="button" disabled={pending||view.page.offset===0} onClick={()=>void load(Math.max(0,view.page.offset-50),view.page.head_hash)}>Previous page</button>
   <button className="health-link" type="button" disabled={pending||view.page.next_offset===null} onClick={()=>view.page.next_offset!==null&&void load(view.page.next_offset,view.page.head_hash)}>Next page</button>
  </div>}
 </section>;
}

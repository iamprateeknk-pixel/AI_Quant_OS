"use client";

import {useEffect, useRef, useState} from "react";
import {acceptRealtimeFrame, parseRealtimeFrame, type RealtimeFrame} from "./frames";

type State = "CONNECTING" | "CURRENT" | "STALE" | "DISCONNECTED" | "WITHHELD";
export function RealtimePanel() {
  const [frame,setFrame]=useState<RealtimeFrame|null>(null),[state,setState]=useState<State>("CONNECTING");
  const previous=useRef<RealtimeFrame|null>(null);
  useEffect(()=>{
    const source=new EventSource("/api/realtime/events");
    const receive=(event:MessageEvent,reset:boolean)=>{
      try{
        if(typeof event.data!=="string" || event.data.length>33000) throw new Error("Invalid event");
        const next=parseRealtimeFrame(JSON.parse(event.data),Date.now());
        if(!next || event.lastEventId!==`${next.epoch}:${next.sequence}` || !acceptRealtimeFrame(previous.current,next,reset)) throw new Error("Invalid state");
        previous.current=next;setFrame(next);setState("CURRENT");
      }catch{setState("WITHHELD");}
    };
    const reset=(event:MessageEvent)=>receive(event,true),update=(event:MessageEvent)=>receive(event,false);
    source.addEventListener("reset",reset);source.addEventListener("snapshot",update);
    source.onerror=()=>setState("DISCONNECTED");
    return()=>{source.removeEventListener("reset",reset);source.removeEventListener("snapshot",update);source.close();};
  },[]);
  useEffect(()=>{
    if(!frame) return;
    const timer=setTimeout(()=>setState(current=>current==="CURRENT"?"STALE":current),Math.max(0,Date.parse(frame.valid_until)-Date.now()));
    return()=>clearTimeout(timer);
  },[frame]);
  const current=state==="CURRENT" && frame!==null;
  const label=current?"Current paper snapshot":state==="CONNECTING"?"Connecting":state==="STALE"?"Snapshot expired":state==="WITHHELD"?"Update withheld":"Disconnected";
  return <section className="panel content-panel" aria-labelledby="realtime-heading">
    <div className="panel-heading"><h2 id="realtime-heading">Current paper operations</h2><span role="status">{label}</span></div>
    <p className="panel-muted">Simulated paper state updates automatically while your operator session is active. Sign in through Risk Center if the feed is disconnected.</p>
    {!current && <p className="notice warning">Current state is unknown. {frame?"The table below is the last accepted observation.":"No current observation has been received."}</p>}
    {frame && <><dl className="fact-list"><div><dt>{current?"Safety state":"Last observed safety state"}</dt><dd>{frame.safety_state}</dd></div><div><dt>Recorded paper orders</dt><dd>{frame.order_count}</dd></div><div><dt>Observed at</dt><dd><time dateTime={frame.captured_at}>{frame.captured_at}</time></dd></div></dl>
      <div className="table-scroll"><table><caption>{current?"Current simulated evidence":"Last accepted simulated evidence"}</caption><thead><tr><th>Instrument</th><th>Quote</th><th>History</th><th>Bars</th><th>Regime</th></tr></thead><tbody>{frame.instruments.map(row=><tr key={row.instrument_id}><td>{row.instrument_id}</td><td>{row.quote_status}</td><td>{row.history_status}</td><td>{row.bar_count}</td><td>{row.regime}</td></tr>)}</tbody></table></div></>}
    <p className="panel-muted">This feed reports state. Safety actions still require their own backend acknowledgement. Live routing is disabled.</p>
  </section>;
}

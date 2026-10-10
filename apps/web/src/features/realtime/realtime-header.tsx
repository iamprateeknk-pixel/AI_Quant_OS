"use client";

import {useRealtime} from "./use-realtime";

export function RealtimeHeader() {
  const {state,frame}=useRealtime(),current=state==="CURRENT" && frame!==null;
  return <div className="topbar-state" role="status">
    {current && frame.safety_state==="NORMAL" && <span className="state-dot" aria-hidden="true" />}
    <span>{current?`PAPER · ${frame.safety_state}`:state==="CONNECTING"?"PAPER · CONNECTING":"PAPER · CURRENT STATE UNKNOWN"}</span>
  </div>;
}

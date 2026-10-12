"use client";
import {useSyncExternalStore} from "react";
import {subscribeRealtime,readRealtimeView} from "@/features/realtime/use-realtime";
import {createMonitoringLiveStore} from "./monitoring-live-store";
import {readLiveMonitoring} from "./monitoring-live-read";
import {MonitoringAlertPanel} from "./monitoring-alert-panel";

const store=createMonitoringLiveStore({clock:Date.now,signal:readRealtimeView,subscribeTrigger:subscribeRealtime,read:readLiveMonitoring,schedule:(callback,delay)=>window.setTimeout(callback,delay),cancel:id=>window.clearTimeout(id)});
type Read={ok:true;value:unknown}|{ok:false;reason:string};
export function MonitoringLivePanel({initialResult,observedAt}:{initialResult:Read;observedAt:string}) {
  const view=useSyncExternalStore(store.subscribe,store.snapshot,store.serverSnapshot);
  if(view.state==="SERVER")return <MonitoringAlertPanel result={initialResult} observedAt={observedAt}/>;
  return <>
    <p className="panel-muted" role="status">{view.state==="CURRENT"?"Monitoring refreshes every 15 seconds while the authenticated paper stream is current. Source timestamps remain independent.":"Current monitoring observations are withheld while the stream or protected read is unavailable. No rule states are inferred."}</p>
    <MonitoringAlertPanel result={view.read??{ok:false,reason:"UNAVAILABLE"}} observedAt={view.viewedAt??observedAt} automatic={view.state==="CURRENT"}/>
  </>;
}

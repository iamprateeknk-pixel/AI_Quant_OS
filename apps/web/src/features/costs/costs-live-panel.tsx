"use client";
import {useSyncExternalStore} from "react";
import {subscribeRealtime,readRealtimeView} from "@/features/realtime/use-realtime";
import {createCostsLiveStore} from "./costs-live-store";
import {readLiveCosts} from "./costs-live-read";
import {CallBudgetPanel} from "./call-budget-panel";
import {SpendingPanels} from "./shared-spending-panel";

type Read={ok:true;value:unknown}|{ok:false;reason:string};
const store=createCostsLiveStore({clock:Date.now,signal:readRealtimeView,subscribeTrigger:subscribeRealtime,read:readLiveCosts,schedule:(callback,delay)=>window.setTimeout(callback,delay),cancel:id=>window.clearTimeout(id)});
export function CostsLivePanel({initialReads}:{initialReads:[Read,Read,Read]}){
 const view=useSyncExternalStore(store.subscribe,store.snapshot,store.serverSnapshot);
 const live=view.state==="CURRENT"?view.snapshot:null;
 const unavailable:Read={ok:false,reason:"UNAVAILABLE"};
 const reads: [Read,Read,Read]=live?[live.callBudget,live.reviewSpending,live.sharedSpending]:view.state==="SERVER"?initialReads:[unavailable,unavailable,unavailable];
 const automatic=view.state==="CURRENT";
 return <>
  {view.state!=="SERVER"&&<p className="panel-muted" role="status">{automatic?"Protected cost observations refresh every 15 seconds while the authenticated paper stream is current. Call capacity and provider ledgers are read separately; their snapshots are not atomic, and declared capacity is not an invoice.":"Protected cost observations are withheld while the authenticated stream or read is unavailable. No missing usage or cost is inferred."}</p>}
  <SpendingPanels reviewResult={reads[1]} sharedResult={reads[2]} automatic={automatic} />
  <CallBudgetPanel result={reads[0]} />
 </>;
}

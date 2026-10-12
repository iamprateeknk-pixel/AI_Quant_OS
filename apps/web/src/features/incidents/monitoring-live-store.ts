import {parseMonitoringAlerts, monitoringMicros, type MonitoringAlerts} from "./monitoring-alerts";

type Read = {ok:true;value:unknown}|{ok:false;reason:string};
type Signal = {state:string;frame:{epoch:string;sequence:number;captured_at:string;valid_until:string}|null};
export type MonitoringLiveView = {state:"SERVER"|"WAITING"|"CURRENT"|"WITHHELD";read:Read|null;viewedAt:string|null};
type Options = {clock:()=>number;signal:()=>Signal;subscribeTrigger:(callback:()=>void)=>()=>void;read:(signal:AbortSignal)=>Promise<Read>;schedule:(callback:()=>void,delay:number)=>number;cancel:(id:number)=>void};
const INITIAL:MonitoringLiveView=Object.freeze({state:"SERVER",read:null,viewedAt:null});

/** Shared browser observer; source timestamps and expiry never inherit a stream lease. */
export function createMonitoringLiveStore(options:Options) {
  let view=INITIAL,detach:(()=>void)|null=null,epoch:string|null=null,sequence=0,generation=0;
  let pending:AbortController|null=null,expiry:number|null=null,poll:number|null=null,deadline:number|null=null;
  let nextRead=0,lastObservation:bigint|null=null;
  const listeners=new Set<()=>void>();
  const publish=(next:MonitoringLiveView)=>{view=next;for(const listener of [...listeners])listener();};
  const cancel=(id:number|null)=>{if(id!==null)options.cancel(id);};
  const stopRead=()=>{generation++;pending?.abort();pending=null;cancel(deadline);deadline=null;};
  const withhold=()=>{cancel(expiry);expiry=null;publish({state:"WITHHELD",read:null,viewedAt:null});};
  const freshSignal=()=>{
    const signal=options.signal(),frame=signal.frame,now=options.clock();
    return signal.state==="CURRENT" && frame && Number.isFinite(now) && Number.isSafeInteger(frame.sequence) && frame.sequence>0 && /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(frame.epoch) && Date.parse(frame.captured_at)<=now && now<Date.parse(frame.valid_until) ? frame : null;
  };
  const schedulePoll=()=>{cancel(poll);poll=options.schedule(()=>{poll=null;if(listeners.size)request();},Math.max(0,nextRead-options.clock()));};
  const request=()=>{
    const frame=freshSignal();
    if(!frame || frame.epoch!==epoch || frame.sequence<sequence){stopRead();withhold();return;}
    if(pending || !listeners.size)return;
    if(options.clock()<nextRead){schedulePoll();return;}
    nextRead=options.clock()+15000;
    const owner=new AbortController(),identity=++generation;pending=owner;
    deadline=options.schedule(()=>{if(pending===owner){stopRead();withhold();schedulePoll();}},4000);
    void options.read(owner.signal).then(result=>{
      if(identity!==generation || pending!==owner || !listeners.size)return;
      const current=freshSignal();
      if(!current || current.epoch!==epoch){stopRead();withhold();return;}
      cancel(deadline);deadline=null;pending=null;
      const now=options.clock(),snapshot:MonitoringAlerts|null=result.ok?parseMonitoringAlerts(result.value):null;
      const captured=snapshot?Date.parse(snapshot.observed_at):NaN;
      const originalTime=snapshot?monitoringMicros(snapshot.observed_at):null;
      if(!snapshot || originalTime===null || !Number.isFinite(captured) || captured>now || now-captured>=45000 || (lastObservation!==null && originalTime<lastObservation)){
        withhold();
        if(!result.ok && result.reason==="SIGN_IN")publish({state:"WITHHELD",read:{ok:false,reason:"SIGN_IN"},viewedAt:new Date(now).toISOString()});
      }else{
        lastObservation=originalTime;cancel(expiry);
        publish({state:"CURRENT",read:{ok:true,value:snapshot},viewedAt:new Date(now).toISOString()});
        expiry=options.schedule(()=>{expiry=null;withhold();},captured+45000-now);
      }
      schedulePoll();
    }).catch(()=>{
      if(identity!==generation || pending!==owner)return;
      stopRead();withhold();schedulePoll();
    });
  };
  const receive=()=>{
    const frame=freshSignal();
    if(!frame){stopRead();cancel(poll);poll=null;withhold();return;}
    if(epoch!==frame.epoch){stopRead();withhold();epoch=frame.epoch;sequence=0;lastObservation=null;nextRead=0;}
    if(frame.sequence<=sequence){stopRead();withhold();return;}
    sequence=frame.sequence;request();
  };
  return {
    snapshot:()=>view,serverSnapshot:()=>INITIAL,
    subscribe(listener:()=>void){
      const notify=()=>listener();listeners.add(notify);
      if(listeners.size===1){publish({state:"WAITING",read:null,viewedAt:null});detach=options.subscribeTrigger(receive);receive();}
      return()=>{listeners.delete(notify);if(!listeners.size){detach?.();detach=null;stopRead();cancel(expiry);cancel(poll);expiry=null;poll=null;epoch=null;sequence=0;lastObservation=null;nextRead=0;view={state:"WITHHELD",read:null,viewedAt:null};}};
    },
  };
}

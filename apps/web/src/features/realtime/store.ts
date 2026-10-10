import {acceptRealtimeFrame, parseRealtimeFrame, type RealtimeFrame} from "./frames";

export type RealtimeView = {state:"CONNECTING"|"CURRENT"|"STALE"|"DISCONNECTED"|"WITHHELD";frame:RealtimeFrame|null};
type Source = {
  addEventListener(kind:"reset"|"snapshot",listener:(event:MessageEvent)=>void):void;
  removeEventListener(kind:"reset"|"snapshot",listener:(event:MessageEvent)=>void):void;
  close():void;
  onerror:((event:Event)=>void)|null;
};
type Options = {connect:()=>Source;clock:()=>number;schedule:(callback:()=>void,delay:number)=>number;cancel:(id:number)=>void};
const INITIAL:RealtimeView=Object.freeze({state:"CONNECTING",frame:null});

/** One connection and original-lease timer, owned by the active subscriptions. */
export function createRealtimeStore(options:Options) {
  let view=INITIAL,source:Source|null=null,detach:(()=>void)|null=null;
  let expiry:number|null=null,retry:number|null=null,failures=0;
  const listeners=new Set<()=>void>();
  const publish=(next:RealtimeView)=>{view=next;for(const listener of [...listeners])listener();};
  const clearExpiry=()=>{if(expiry!==null)options.cancel(expiry);expiry=null;};
  const close=()=>{const cleanup=detach;source=null;detach=null;cleanup?.();};
  const retryConnection=()=>{
    clearExpiry();close();publish({...view,state:"DISCONNECTED"});
    if(!listeners.size || retry!==null) return;
    const delay=Math.min(30000,2000*2**Math.min(failures++,4));
    retry=options.schedule(()=>{retry=null;start();},delay);
  };
  const start=()=>{
    if(!listeners.size || source!==null) return;
    try{
      const active=options.connect();source=active;
      const receive=(event:MessageEvent,reset:boolean)=>{
        if(source!==active) return;
        try{
          if(typeof event.data!=="string" || event.data.length>33000) throw new Error("Invalid realtime event");
          const frame=parseRealtimeFrame(JSON.parse(event.data),options.clock());
          if(!frame || event.lastEventId!==`${frame.epoch}:${frame.sequence}` || !acceptRealtimeFrame(view.frame,frame,reset)) throw new Error("Invalid realtime state");
          failures=0;clearExpiry();publish({state:"CURRENT",frame});
          expiry=options.schedule(()=>{expiry=null;if(view.frame===frame && view.state==="CURRENT")publish({state:"STALE",frame});},Math.max(0,Date.parse(frame.valid_until)-options.clock()));
        }catch{publish({...view,state:"WITHHELD"});}
      };
      const reset=(event:MessageEvent)=>receive(event,true),snapshot=(event:MessageEvent)=>receive(event,false);
      detach=()=>{active.removeEventListener("reset",reset);active.removeEventListener("snapshot",snapshot);active.onerror=null;active.close();};
      active.addEventListener("reset",reset);active.addEventListener("snapshot",snapshot);
      active.onerror=()=>{if(source===active)retryConnection();};
    }catch{retryConnection();}
  };
  return {
    snapshot:()=>view,
    serverSnapshot:()=>INITIAL,
    subscribe(listener:()=>void){
      const notify=()=>listener();listeners.add(notify);if(listeners.size===1)start();
      return()=>{
        listeners.delete(notify);
        if(!listeners.size){clearExpiry();if(retry!==null)options.cancel(retry);retry=null;close();view={...view,state:"DISCONNECTED"};}
      };
    },
  };
}

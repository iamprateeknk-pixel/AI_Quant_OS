import {test} from "node:test";
import assert from "node:assert/strict";
import {existsSync,readFileSync} from "node:fs";
import ts from "typescript";
const compile=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString("base64")}`;
async function module(){
 const path=new URL("../src/features/incidents/monitoring-live-store.ts",import.meta.url);
 assert(existsSync(path),"Realtime-triggered monitoring store required");
 const parser=compile(readFileSync(new URL("../src/features/incidents/monitoring-alerts.ts",import.meta.url),"utf8"));
 return import(compile(readFileSync(path,"utf8").replace('from "./monitoring-alerts"',`from "${parser}"`)));
}
const native=JSON.parse(readFileSync(new URL("./fixtures/monitoring-alerts.json",import.meta.url),"utf8"));
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function fixture(){
 let now=Date.parse("2026-10-12T00:00:00Z"),seq=0,signal={state:"CONNECTING",frame:null},next=0;
 const timers=new Map(),subscribers=new Set(),calls=[],pending=[];
 const epoch="12345678-1234-4123-8123-123456789abc";
 const options={clock:()=>now,signal:()=>signal,subscribeTrigger(callback){subscribers.add(callback);return()=>subscribers.delete(callback);},schedule(callback,delay){const id=++next;timers.set(id,{callback,at:now+delay});return id;},cancel(id){timers.delete(id);},read(abort){calls.push(abort);return new Promise(resolve=>pending.push(resolve));}};
 return {options,calls,pending,timers,subscribers,now:()=>now,advance(ms){now+=ms;for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.callback();}},trigger(state="CURRENT",nextEpoch=epoch){signal={state,frame:state==="CURRENT"?{epoch:nextEpoch,sequence:++seq,captured_at:new Date(now).toISOString(),valid_until:new Date(now+3000).toISOString()}:null};for(const cb of [...subscribers])cb();},reply(index=0,state="PENDING"){const value=structuredClone(native);value.observed_at=new Date(now).toISOString();value.rules=value.rules.map((r,i)=>({...r,state:i===0?state:"INACTIVE",evaluated_at:value.observed_at,active_since:i===0?new Date(now-(state==="FIRING"?180000:30000)).toISOString():null}));pending[index]({ok:true,value});return value;}};
}
test("all monitoring subscribers share one triggered read and dispose original timers",async()=>{
 const {createMonitoringLiveStore:create}=await module(),f=fixture(),store=create(f.options);
 assert.equal(store.serverSnapshot().state,"SERVER");const notify=()=>{},a=store.subscribe(notify),b=store.subscribe(notify);assert.equal(f.subscribers.size,1);assert.equal(f.calls.length,0);
 f.trigger();assert.equal(f.calls.length,1);f.reply();await settle();assert.equal(store.snapshot().state,"CURRENT");
 f.trigger();assert.equal(f.calls.length,1);a();assert.equal(f.subscribers.size,1);b();assert.equal(f.subscribers.size,0);assert.equal(f.timers.size,0);
});
test("stream disconnect clears current rows aborts original read and rejects late completion",async()=>{
 const {createMonitoringLiveStore:create}=await module(),f=fixture(),store=create(f.options),off=store.subscribe(()=>{});
 f.trigger();f.trigger("DISCONNECTED");assert(f.calls[0].aborted);f.reply();await settle();assert.equal(store.snapshot().state,"WITHHELD");assert.equal(store.snapshot().read,null);off();
});
test("new stream epoch invalidates the old response even if cancellation is ignored",async()=>{
 const {createMonitoringLiveStore:create}=await module(),f=fixture(),store=create(f.options),off=store.subscribe(()=>{});
 f.trigger();f.trigger("CURRENT","87654321-1234-4123-8123-123456789abc");assert.equal(f.calls.length,2);assert(f.calls[0].aborted);
 f.reply(0);await settle();assert.notEqual(store.snapshot().state,"CURRENT");f.reply(1);await settle();assert.equal(store.snapshot().state,"CURRENT");off();
});
test("unchanged fresh stream frames do not extend the original monitoring expiry",async()=>{
 const {createMonitoringLiveStore:create}=await module(),f=fixture(),store=create(f.options),off=store.subscribe(()=>{});
 f.trigger();const original=f.reply();await settle();
 for(let i=1;i<45;i++){f.advance(1000);f.trigger();if(i%15===0){f.pending.at(-1)({ok:true,value:original});await settle();}assert.equal(store.snapshot().state,"CURRENT");}
 f.advance(1000);f.trigger();
 assert.equal(store.snapshot().state,"WITHHELD");assert.equal(store.snapshot().read,null);off();
});
test("a regressing original microsecond timestamp is withheld without a new read lease",async()=>{
 const {createMonitoringLiveStore:create}=await module(),f=fixture(),store=create(f.options),off=store.subscribe(()=>{});
 f.advance(1);f.trigger();const original=f.reply();original.observed_at="2026-10-12T00:00:00.000999Z";
 for(const row of original.rules)row.evaluated_at="2026-10-12T00:00:00.000000Z";
 await settle();assert.equal(store.snapshot().state,"CURRENT");
 for(let i=0;i<15;i++){f.advance(1000);f.trigger();}
 const regressed=structuredClone(original);regressed.observed_at="2026-10-12T00:00:00.000998Z";
 // Preserve the original selected times coherently below both observation timestamps.
 for(const row of regressed.rules)row.evaluated_at="2026-10-12T00:00:00.000000Z";
 f.pending.at(-1)({ok:true,value:regressed});await settle();assert.equal(store.snapshot().state,"WITHHELD");assert.equal(store.snapshot().read,null);off();
});
test("pending advances to firing only after a new protected observation",async()=>{
 const {createMonitoringLiveStore:create}=await module(),f=fixture(),store=create(f.options),off=store.subscribe(()=>{});
 f.trigger();f.reply();await settle();assert.equal(store.snapshot().read.value.rules[0].state,"PENDING");
 f.advance(15000);f.trigger();f.reply(1,"FIRING");await settle();assert.equal(store.snapshot().read.value.rules[0].state,"FIRING");off();
});
test("read deadline and last unsubscribe abort only original work and retain no private cache",async()=>{
 const {createMonitoringLiveStore:create}=await module(),f=fixture(),store=create(f.options),off=store.subscribe(()=>{});
 f.trigger();f.advance(4000);assert(f.calls[0].aborted);assert.equal(store.snapshot().state,"WITHHELD");f.reply();await settle();assert.equal(store.snapshot().read,null);off();assert.equal(f.timers.size,0);
 const second=store.subscribe(()=>{});assert.notEqual(store.snapshot().state,"CURRENT");second();assert.equal(f.timers.size,0);
});
test("actual shared realtime store keeps one source and immediately withholds monitoring on disconnect",async()=>{
 const {createMonitoringLiveStore:create}=await module(),f=fixture();
 const frames=compile(readFileSync(new URL("../src/features/realtime/frames.ts",import.meta.url),"utf8"));
 const {createRealtimeStore}=await import(compile(readFileSync(new URL("../src/features/realtime/store.ts",import.meta.url),"utf8").replace('from "./frames"',`from "${frames}"`)));
 const sources=[];
 class Source{listeners=new Map();onerror=null;closed=false;addEventListener(type,callback){this.listeners.set(type,callback);}removeEventListener(type){this.listeners.delete(type);}close(){this.closed=true;}}
 const channel=createRealtimeStore({...f.options,connect(){const source=new Source();sources.push(source);return source;}});
 const headerOff=channel.subscribe(()=>{});
 const store=create({...f.options,signal:channel.snapshot,subscribeTrigger:channel.subscribe}),off=store.subscribe(()=>{});await settle();assert.equal(sources.length,1);
 const frame=JSON.parse(readFileSync(new URL("./fixtures/realtime-snapshot.json",import.meta.url),"utf8"));
 frame.captured_at=new Date(f.now()).toISOString();frame.valid_until=new Date(f.now()+3000).toISOString();frame.world.as_of=frame.captured_at;frame.world.valid_until=new Date(f.now()+5000).toISOString();
 sources[0].listeners.get("reset")({data:JSON.stringify(frame),lastEventId:`${frame.epoch}:${frame.sequence}`});assert.equal(f.calls.length,1);f.reply();await settle();assert.equal(store.snapshot().state,"CURRENT");
 sources[0].onerror(new Event("error"));assert.equal(store.snapshot().read,null);assert.equal(store.snapshot().state,"WITHHELD");assert(sources[0].closed);
 off();headerOff();assert.equal(f.timers.size,0);
});

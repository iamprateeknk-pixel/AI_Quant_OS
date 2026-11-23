import {test} from "node:test";
import assert from "node:assert/strict";
import {existsSync,readFileSync} from "node:fs";
import ts from "typescript";
const compile=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString("base64")}`;
async function module(){const path=new URL("../src/features/costs/costs-live-store.ts",import.meta.url);assert(existsSync(path),"Realtime Costs store required");return import(compile(readFileSync(path,"utf8")));}
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function fixture(){
 let now=Date.parse("2026-10-12T00:00:00Z"),sequence=0,signal={state:"CONNECTING",frame:null},timerId=0;
 const timers=new Map(),subscribers=new Set(),pending=[],calls=[],epoch="12345678-1234-4123-8123-123456789abc";
 const options={
  clock:()=>now,signal:()=>signal,
  subscribeTrigger(callback){subscribers.add(callback);return()=>subscribers.delete(callback);},
  schedule(callback,delay){const id=++timerId;timers.set(id,{callback,at:now+delay});return id;},
  cancel(id){timers.delete(id);},
  read(abort){calls.push(abort);return new Promise(resolve=>pending.push(resolve));},
 };
 return{
  now:()=>now,timers,subscribers,pending,calls,options,
  advance(ms){now+=ms;for(const[id,timer]of[...timers])if(timer.at<=now){timers.delete(id);timer.callback();}},
  trigger(state="CURRENT",nextEpoch=epoch){signal={state,frame:state==="CURRENT"?{epoch:nextEpoch,sequence:++sequence,captured_at:new Date(now).toISOString(),valid_until:new Date(now+3000).toISOString()}:null};for(const callback of[...subscribers])callback();},
  reply(index=0,observed=now){pending[index]({observed_at:new Date(observed).toISOString(),callBudget:{ok:true,value:{state:"ENFORCED"}},reviewSpending:{ok:false,reason:"UNAVAILABLE"},sharedSpending:{ok:true,value:{state:"ENFORCED"}}});},
 };
}
test("Costs subscribers share one protected read and refresh no faster than every 15 seconds",async()=>{const{createCostsLiveStore}=await module(),f=fixture(),store=createCostsLiveStore(f.options);const a=store.subscribe(()=>{}),b=store.subscribe(()=>{});assert.equal(f.subscribers.size,1);f.trigger();assert.equal(f.calls.length,1);f.reply();await settle();assert.equal(store.snapshot().state,"CURRENT",JSON.stringify({view:store.snapshot(),signal:f.options.signal()}));f.advance(1000);f.trigger();assert.equal(f.calls.length,1);f.advance(13000);f.trigger();f.advance(1000);assert.equal(f.calls.length,2);f.reply(1);await settle();assert.equal(store.snapshot().state,"CURRENT");a();b();assert.equal(f.timers.size,0);});
test("Costs observations are withheld on stream disconnect and late reads cannot restore them",async()=>{const{createCostsLiveStore}=await module(),f=fixture(),store=createCostsLiveStore(f.options),off=store.subscribe(()=>{});f.trigger();f.trigger("DISCONNECTED");assert(f.calls[0].aborted);f.reply();await settle();assert.equal(store.snapshot().state,"WITHHELD");assert.equal(store.snapshot().snapshot,null);off();});
test("Costs read deadline aborts the original request and ignores its late result",async()=>{const{createCostsLiveStore}=await module(),f=fixture(),store=createCostsLiveStore(f.options),off=store.subscribe(()=>{});f.trigger();f.advance(4000);assert(f.calls[0].aborted);assert.equal(store.snapshot().state,"WITHHELD");f.reply();await settle();assert.equal(store.snapshot().snapshot,null);off();});
test("expired and future Costs observations are withheld",async()=>{const{createCostsLiveStore}=await module();for(const offset of[-46000,1000]){const f=fixture(),store=createCostsLiveStore(f.options),off=store.subscribe(()=>{});f.trigger();f.reply(0,f.now()+offset);await settle();assert.equal(store.snapshot().state,"WITHHELD");assert.equal(store.snapshot().snapshot,null);off();}});

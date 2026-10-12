import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import ts from "typescript";
const compile=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString("base64")}`;
const parser=compile(readFileSync(new URL("../src/features/incidents/monitoring-alerts.ts",import.meta.url),"utf8"));
const native=JSON.parse(readFileSync(new URL("./fixtures/monitoring-alerts.json",import.meta.url),"utf8"));

test("private monitoring route selects the contract, rejects query overrides and sanitizes failures",async()=>{
 const bridge=compile('export async function readMonitoringAlerts(){return globalThis.monitoringBoundaryRead();}');
 const source=readFileSync(new URL("../src/app/api/incidents/monitoring/route.ts",import.meta.url),"utf8").replace('from "@/features/operator-session/server"',`from "${bridge}"`).replace('from "@/features/incidents/monitoring-alerts"',`from "${parser}"`);
 const {GET}=await import(compile(source)),old=globalThis.monitoringBoundaryRead;let calls=0;
 try{
  globalThis.monitoringBoundaryRead=async()=>{calls++;return {ok:true,value:{...native,credential:"private"}};};
  assert.equal((await GET(new Request("http://local/api/incidents/monitoring?url=https://private.invalid"))).status,422);assert.equal(calls,0);
  const response=await GET(new Request("http://local/api/incidents/monitoring"));assert.equal(response.status,200);assert.equal(response.headers.get("cache-control"),"no-store");assert.deepEqual(await response.json(),native);
  globalThis.monitoringBoundaryRead=async()=>({ok:false,reason:"SIGN_IN"});assert.equal((await GET(new Request("http://local/api/incidents/monitoring"))).status,401);
  globalThis.monitoringBoundaryRead=async()=>({ok:true,value:{...native,durable:true}});assert.equal((await GET(new Request("http://local/api/incidents/monitoring"))).status,503);
  globalThis.monitoringBoundaryRead=async()=>{throw new Error("private-error");};const bad=await GET(new Request("http://local/api/incidents/monitoring"));assert.equal(bad.status,503);assert(!((await bad.text()).includes("private")));
 }finally{if(old===undefined)delete globalThis.monitoringBoundaryRead;else globalThis.monitoringBoundaryRead=old;}
});
test("browser monitoring read is fixed same-origin bounded cancellable and never returns raw credentials",async()=>{
 const source=readFileSync(new URL("../src/features/incidents/monitoring-live-read.ts",import.meta.url),"utf8").replace('from "./monitoring-alerts"',`from "${parser}"`);
 const {readLiveMonitoring}=await import(compile(source)),old=globalThis.fetch,calls=[],controller=new AbortController();
 try{
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return Response.json({...native,raw_error:"private"});};
  assert.deepEqual(await readLiveMonitoring(controller.signal),{ok:true,value:native});assert.equal(calls[0].url,"/api/incidents/monitoring");assert.equal(calls[0].options.credentials,"same-origin");assert.equal(calls[0].options.cache,"no-store");assert.equal(calls[0].options.redirect,"error");assert.equal(calls[0].options.signal,controller.signal);assert.equal(calls[0].options.headers,undefined);
  globalThis.fetch=async()=>new Response("x".repeat(8193));await assert.rejects(()=>readLiveMonitoring(controller.signal));
  globalThis.fetch=async()=>new Response(null,{status:401});assert.deepEqual(await readLiveMonitoring(controller.signal),{ok:false,reason:"SIGN_IN"});
  globalThis.fetch=async()=>new Response(null,{status:503});assert.deepEqual(await readLiveMonitoring(controller.signal),{ok:false,reason:"UNAVAILABLE"});
 }finally{globalThis.fetch=old;}
});

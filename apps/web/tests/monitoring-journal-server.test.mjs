import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import ts from "typescript";

const compiler={module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022};
const data=text=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(text,{compilerOptions:{...compiler}}).outputText).toString("base64")}`;
const read=name=>readFileSync(new URL(`../src/${name}`,import.meta.url),"utf8");
const head="a".repeat(64);
const page={schema_version:"1.0",mode:"PAPER",source_kind:"LOCAL_PROMETHEUS_RULE_EVALUATION",durable:true,notification_verified:false,independently_attested:false,head_hash:head,total:0,offset:0,limit:50,next_offset:null,episodes:[]};
const receipt={schema_version:"1.0",mode:"PAPER",committed:true,control_authorized:false,action_id:"10bb3203-550b-4cf1-bcca-83f764e1d009",episode_id:"4f2e03a6-7dda-560a-b4fe-64e82af8b0b1",actor_session_id:"20bb3203-550b-4cf1-bcca-83f764e1d009",acknowledged_at:"2026-10-12T00:00:01+00:00",source_head:head};

async function server(){
 const boundary=data(read("features/operator-session/boundary.ts"));
 const journal=data(read("features/incidents/monitoring-journal.ts").replace('from "./monitoring-alerts"',`from ${JSON.stringify(data(read("features/incidents/monitoring-alerts.ts")))}`));
 const source=read("features/operator-session/server.ts").replace('import {parsePaperReconciliation} from "../orders/reconciliation";','').replace('import "server-only";','')
  .replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.monitorCookies();')
  .replace('import { NextResponse } from "next/server";','const NextResponse={json:(value,init)=>Response.json(value,init)};')
  .replace('from "./boundary"',`from ${JSON.stringify(boundary)}`)
  .replaceAll('import("../incidents/monitoring-journal")',`import(${JSON.stringify(journal)})`);
 return import(data(source));
}
const request=(url,body,headers={})=>new Request(url,{method:"POST",headers:{Origin:"http://127.0.0.1:3000",Host:"127.0.0.1:3000","Content-Type":"application/json",...headers},body:JSON.stringify(body)});

test("history reads use the current private session and fixed bounded page",async()=>{
 const oldFetch=globalThis.fetch,oldCookies=globalThis.monitorCookies,calls=[];
 try{
  const api=await server();globalThis.monitorCookies=()=>({get:()=>undefined});globalThis.fetch=async(url,options)=>{calls.push({url,options});return Response.json(new URL(url).searchParams.get("offset")==="50"?{...page,offset:50}:page);};
  assert.deepEqual(await api.readMonitoringIncidents(),{ok:false,reason:"SIGN_IN"});assert.equal(calls.length,0);
  globalThis.monitorCookies=()=>({get:()=>({value:"private-session"})});
  const result=await api.readMonitoringIncidents(50,head);assert.equal(result.ok,true);
  assert.equal(calls.length,1);assert.equal(new URL(calls[0].url).pathname,"/api/v1/observability/incidents");
  assert.equal(new URL(calls[0].url).search,"?limit=50&offset=50&head="+head);
  assert.equal(calls[0].options.headers["X-Operator-Session"],"private-session");
  assert.equal(calls[0].options.cache,"no-store");assert.equal(calls[0].options.redirect,"error");
  assert.deepEqual(await api.readMonitoringIncidents(1),{ok:false,reason:"INVALID_PAGE"});
  globalThis.fetch=async()=>new Response(null,{status:409});
  assert.deepEqual(await api.readMonitoringIncidents(0),{ok:false,reason:"CHANGED"});
 }finally{globalThis.fetch=oldFetch;if(oldCookies===undefined)delete globalThis.monitorCookies;else globalThis.monitorCookies=oldCookies;}
});

test("review submission enforces origin, session, exact body and allowlisted receipt",async()=>{
 const oldFetch=globalThis.fetch,oldCookies=globalThis.monitorCookies,calls=[];
 try{
  const api=await server();globalThis.monitorCookies=()=>({get:()=>({value:"private-session"})});
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return Response.json(receipt);};
  const input={episode_id:receipt.episode_id,action_id:receipt.action_id,expected_head:head};
  const result=await api.submitMonitoringAcknowledgement(request("http://127.0.0.1:3000/api/incidents/journal/acknowledge",input));
  assert.equal(result.status,200);assert.deepEqual(await result.json(),receipt);assert.equal(calls.length,1);
  assert.equal(new URL(calls[0].url).pathname,"/api/v1/observability/incidents/acknowledge");
  assert.equal(calls[0].options.headers["X-Operator-Session"],"private-session");
  const badOrigin=await api.submitMonitoringAcknowledgement(request("http://127.0.0.1:3000/api/incidents/journal/acknowledge",input,{Origin:"https://other.invalid"}));
  assert.equal(badOrigin.status,403);assert.equal(calls.length,1);
  const extra=await api.submitMonitoringAcknowledgement(request("http://127.0.0.1:3000/api/incidents/journal/acknowledge",{...input,operator_token:"private"}));
  assert.equal(extra.status,422);assert.equal(calls.length,1);
  globalThis.fetch=async()=>new Response(null,{status:409});
  const stale=await api.submitMonitoringAcknowledgement(request("http://127.0.0.1:3000/api/incidents/journal/acknowledge",input));
  assert.equal(stale.status,409);assert.match((await stale.json()).detail,/History changed/);
 }finally{globalThis.fetch=oldFetch;if(oldCookies===undefined)delete globalThis.monitorCookies;else globalThis.monitorCookies=oldCookies;}
});

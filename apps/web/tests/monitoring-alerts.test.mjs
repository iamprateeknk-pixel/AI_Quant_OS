import {test} from "node:test";
import assert from "node:assert/strict";
import {existsSync,readFileSync} from "node:fs";
import ts from "typescript";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {createRequire} from "node:module";
import {pathToFileURL} from "node:url";
const require=createRequire(import.meta.url);
const compile=(source,jsx=false)=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:jsx?ts.JsxEmit.ReactJSX:undefined}}).outputText.replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))).toString("base64")}`;
const read=name=>readFileSync(new URL(`../src/features/incidents/${name}`,import.meta.url),"utf8");
async function modules(){
 assert(existsSync(new URL("../src/features/incidents/monitoring-alerts.ts",import.meta.url)),"Current monitoring observation decoder required");
 const parser=compile(read("monitoring-alerts.ts"));
 return {...await import(parser),...await import(compile(read("monitoring-alert-panel.tsx").replace('from "./monitoring-alerts"',`from "${parser}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href)),true))};
}
const fixture=()=>JSON.parse(readFileSync(new URL("./fixtures/monitoring-alerts.json",import.meta.url),"utf8"));
const render=(Panel,value,observedAt=value.observed_at)=>renderToStaticMarkup(React.createElement(Panel,{result:{ok:true,value},observedAt}));

test("original native protected pending fixture roundtrips and strips foreign payload",async()=>{
 const {parseMonitoringAlerts:parse}=await modules(),v=fixture();
 assert.deepEqual(parse({...v,credential:"private",rules:v.rules.map(r=>({...r,raw_error:"private"}))}),v);
});
test("initial client props are selected before serialization with no foreign fields or reasons",async()=>{
 const {selectMonitoringRead:select}=await modules(),v=fixture();
 assert.deepEqual(select({ok:true,value:{...v,credential:"private",rules:v.rules.map(r=>({...r,raw_error:"private"}))}}),{ok:true,value:v});
 assert.deepEqual(select({ok:false,reason:"private-error"}),{ok:false,reason:"UNAVAILABLE"});
 assert.deepEqual(select({ok:false,reason:"SIGN_IN"}),{ok:false,reason:"SIGN_IN"});
 assert.deepEqual(select({ok:true,value:{...v,durable:true}}),{ok:false,reason:"UNAVAILABLE"});
});
test("rejects changed rule identity authority dates timestamps and hold",async()=>{
 const {parseMonitoringAlerts:parse}=await modules(),v=fixture();
 for(const change of [{state:"AVAILABLE",rules:[]},{durable:true},{notification_verified:true},{independently_attested:true},{observed_at:"2026-02-30T00:00:00Z"},{rules:[v.rules[0],v.rules[0]]},{rules:v.rules.map(r=>({...r,runbook_path:"https://private.invalid"}))},{rules:v.rules.map(r=>({...r,hold_seconds:0}))},{rules:v.rules.map(r=>({...r,evaluated_at:new Date(Date.parse(v.observed_at)-46000).toISOString()}))},{rules:v.rules.map(r=>({...r,evaluated_at:new Date(Date.parse(v.observed_at)+1000).toISOString()}))}])assert.equal(parse({...v,...change}),null);
 const early={...v,rules:v.rules.map(r=>r.state==="PENDING"?{...r,state:"FIRING"}:r)};assert.equal(parse(early),null);
});
test("pending and firing show original UTC times fixed served runbook and no control actions",async()=>{
 const {MonitoringAlertPanel:Panel,parseMonitoringAlerts:parse}=await modules(),v=fixture();
 const pending=render(Panel,v);assert.match(pending,/Pending/);assert.match(pending,/scope="col"/);assert.match(pending,/\/incidents\/runbook#scrape-failed/);assert.match(pending,/Point-in-time/);assert.doesNotMatch(pending,/Acknowledge|Resolve incident|Resume trading|private|HEALTHY/);
 const firing={...v,rules:v.rules.map(r=>r.state==="PENDING"?{...r,state:"FIRING",active_since:new Date(Date.parse(r.evaluated_at)-180000).toISOString()}:r)};
 assert(parse(firing));assert.match(render(Panel,firing),/Firing/);
});
test("disabled unavailable malformed sign-in and stale view show no rule rows",async()=>{
 const {MonitoringAlertPanel:Panel,parseMonitoringAlerts:parse}=await modules(),v=fixture();
 for(const state of ["DISABLED","UNAVAILABLE"]){const value={...v,state,rules:[]};assert(parse(value));assert.doesNotMatch(render(Panel,value),/<tbody>/);assert.equal(parse({...value,rules:v.rules}),null);}
 const stale=render(Panel,v,new Date(Date.parse(v.observed_at)+46000).toISOString());assert.match(stale,/older than 45 seconds/);assert.doesNotMatch(stale,/<tbody>/);
 const future=render(Panel,v,new Date(Date.parse(v.observed_at)-1000).toISOString());assert.doesNotMatch(future,/<tbody>/);
 const sign=renderToStaticMarkup(React.createElement(Panel,{result:{ok:false,reason:"SIGN_IN"},observedAt:v.observed_at}));assert.match(sign,/href="\/risk"/);assert.doesNotMatch(sign,/<tbody>/);
});
test("inactive is an original rule observation without zero incident inventory",async()=>{
 const {MonitoringAlertPanel:Panel,parseMonitoringAlerts:parse}=await modules(),v=fixture();
 v.rules=v.rules.map(r=>({...r,state:"INACTIVE",active_since:null}));assert(parse(v));const html=render(Panel,v);assert.match(html,/Inactive/);assert.doesNotMatch(html,/0 incidents|All clear|HEALTHY/);
});
test("microsecond freshness and firing hold boundaries match the native contract",async()=>{
 const {parseMonitoringAlerts:parse}=await modules(),v=fixture();
 v.observed_at="2026-10-12T00:01:00.000001Z";
 v.rules=v.rules.map(r=>({...r,state:"INACTIVE",active_since:null,evaluated_at:"2026-10-12T00:00:15.000001Z"}));
 assert(parse(v));assert.equal(parse({...v,rules:v.rules.map(r=>({...r,evaluated_at:"2026-10-12T00:00:15.000000Z"}))}),null);
 v.rules[0]={...v.rules[0],state:"FIRING",evaluated_at:v.observed_at,active_since:"2026-10-11T23:59:00.000001Z"};assert(parse(v));
 assert.equal(parse({...v,rules:[{...v.rules[0],active_since:"2026-10-11T23:59:00.000002Z"},v.rules[1]]}),null);
});
test("served runbook anchors and operational read fallbacks expose guidance without raw errors",async()=>{
 const source=readFileSync(new URL("../src/app/incidents/runbook/page.tsx",import.meta.url),"utf8").replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href));
 const Page=(await import(compile(source,true))).default;const html=renderToStaticMarkup(React.createElement(Page));
 assert.match(html,/id="scrape-failed"/);assert.match(html,/id="scrape-missing"/);assert.match(html,/Do not retry an order/);assert.doesNotMatch(html,/<form|javascript:/);
 const loading=(await import(compile(readFileSync(new URL("../src/app/[section]/loading.tsx",import.meta.url),"utf8"),true))).default;
 const error=(await import(compile(readFileSync(new URL("../src/app/[section]/error.tsx",import.meta.url),"utf8"),true))).default;
 assert.match(renderToStaticMarkup(React.createElement(loading)),/aria-busy="true"/);
 const err=renderToStaticMarkup(React.createElement(error,{retry:()=>{},error:new Error("private-stack")}));assert.match(err,/role="alert"/);assert.doesNotMatch(err,/private-stack/);
});
test("server read uses existing private session bounded no-store backend URL",async()=>{
 const boundary=compile(readFileSync(new URL("../src/features/operator-session/boundary.ts",import.meta.url),"utf8"));
 const source=readFileSync(new URL("../src/features/operator-session/server.ts",import.meta.url),"utf8").replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.monitorCookies();').replace('import { NextResponse } from "next/server";','const NextResponse={};').replace('from "./boundary"',`from "${boundary}"`);
 const server=await import(compile(source));assert.equal(typeof server.readMonitoringAlerts,"function");
 const oldFetch=globalThis.fetch,oldCookies=globalThis.monitorCookies,calls=[];
 try{
  globalThis.monitorCookies=()=>({get:()=>undefined});globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(fixture()));};
  assert.deepEqual(await server.readMonitoringAlerts(),{ok:false,reason:"SIGN_IN"});assert.equal(calls.length,0);
  globalThis.monitorCookies=()=>({get:()=>({value:"private-session"})});assert.equal((await server.readMonitoringAlerts()).ok,true);
  assert.equal(new URL(calls[0].url).pathname,"/api/v1/observability/alerts");assert.equal(calls[0].options.headers["X-Operator-Session"],"private-session");assert.equal(calls[0].options.cache,"no-store");assert.equal(calls[0].options.redirect,"error");assert(calls[0].options.signal instanceof AbortSignal);
  globalThis.fetch=async()=>new Response('x'.repeat(8193));assert.deepEqual(await server.readMonitoringAlerts(),{ok:false,reason:"UNAVAILABLE"});
  globalThis.fetch=async()=>new Response(null,{status:401});assert.deepEqual(await server.readMonitoringAlerts(),{ok:false,reason:"SIGN_IN"});
  globalThis.fetch=async()=>{throw new Error("private-source");};assert.deepEqual(await server.readMonitoringAlerts(),{ok:false,reason:"UNAVAILABLE"});
 }finally{globalThis.fetch=oldFetch;if(oldCookies===undefined)delete globalThis.monitorCookies;else globalThis.monitorCookies=oldCookies;}
});

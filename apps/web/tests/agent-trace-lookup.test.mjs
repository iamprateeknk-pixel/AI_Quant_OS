import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync,existsSync} from "node:fs";
import {createRequire} from "node:module";
import {pathToFileURL} from "node:url";
import ts from "typescript";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
const require=createRequire(import.meta.url);
const compile=(source,jsx=false)=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:jsx?ts.JsxEmit.ReactJSX:undefined}}).outputText.replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))).toString("base64")}`;
const read=name=>readFileSync(new URL(`../src/features/traces/${name}`,import.meta.url),"utf8");
const id="11111111-2222-3333-4444-555555555555",other="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const binding={schema_version:"1.0",mode:"PAPER",kind:"OBSERVED_ARCHIVE_SDK_CONTEXT",delivery_verified:false,run_id:id,run_sha256:"a".repeat(64),runtime_id:"b".repeat(32),sdk_version:"1.45.0",sdk_trace_id:"c".repeat(32),sdk_span_id:"d".repeat(16),recorded_at:"2026-10-11T00:00:00Z"};
const value={schema_version:"1.0",mode:"PAPER",delivery_verified:false,independently_attested:false,run_id:id,observed_at:"2026-10-11T00:00:01Z",state:"AVAILABLE",source_head:"e".repeat(64),source_event_count:2,binding};
async function modules(){
 assert(existsSync(new URL("../src/features/traces/agent-trace-lookup.ts",import.meta.url)),"Protected advisory lookup parser required");
 const parser=compile(read("agent-trace-lookup.ts"));
 return {...await import(parser),...await import(compile(read("agent-trace-lookup-panel.tsx").replace('from "./agent-trace-lookup"',`from "${parser}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href)),true))};
}
test("retained context selects safe metadata and binds the exact requested advisory UUID",async()=>{
 const {parseAgentTraceLookup:parse}=await modules();
 assert.deepEqual(parse({...value,credential:"private",binding:{...binding,rationale:"private"}},id),value);
 assert.equal(parse(value,other),null);
 for(const bad of [{...value,delivery_verified:true},{...value,independently_attested:true},{...value,source_event_count:1},{...value,source_event_count:Number.MAX_SAFE_INTEGER+1},{...value,source_head:"bad"},{...value,observed_at:"2026-02-30T00:00:00Z"},{...value,binding:{...binding,run_id:other}},{...value,binding:{...binding,recorded_at:"2026-10-11T00:00:02Z"}},{...value,binding:{...binding,sdk_trace_id:"0".repeat(32)}},{...value,binding:{...binding,sdk_span_id:"bad"}},{...value,binding:{...binding,delivery_verified:true}}])assert.equal(parse(bad,id),null);
});
test("missing and unavailable states infer no trace or source prefix",async()=>{
 const {parseAgentTraceLookup:parse}=await modules();
 for(const state of ["NOT_RECORDED","NOT_FOUND","UNAVAILABLE"]){
  const v={...value,state,binding:null,...(state==="UNAVAILABLE"?{source_head:null,source_event_count:null}:{})};assert.deepEqual(parse(v,id),v);
  assert.equal(parse({...v,binding},id),null);
 }
 assert.equal(parse({...value,state:"NOT_RECORDED",binding:null,source_event_count:0},id),null);
 assert.equal(parse({...value,state:"UNAVAILABLE",binding:null},id),null);
});
test("server-rendered lookup labels the request and exposes no source payload or delivery claim",async()=>{
 const {AgentTraceLookupPanel:Panel}=await modules();
 const html=renderToStaticMarkup(React.createElement(Panel,{runId:id,result:{ok:true,value:{...value,credential:"private",binding:{...binding,task:"private"}}}}));
 assert.match(html,/Advisory run trace/);assert.match(html,/name="run"/);assert.match(html,/id="advisory-run-id"/);assert.match(html,/<label[^>]*for="advisory-run-id"/);assert.match(html,/<form(?=[^>]*method="get")(?=[^>]*action="\/traces")/);
 assert.match(html,/Saved trace context/);assert.match(html,/not proof of delivery/);assert.match(html,new RegExp(binding.sdk_trace_id));assert.match(html,/<time/);assert.doesNotMatch(html,/private|HEALTHY|onclick=/);
 const missing=renderToStaticMarkup(React.createElement(Panel,{runId:id,result:{ok:true,value:{...value,state:"NOT_RECORDED",binding:null}}}));assert.match(missing,/No trace context was recorded/);assert.doesNotMatch(missing,new RegExp(binding.sdk_trace_id));
 const signIn=renderToStaticMarkup(React.createElement(Panel,{runId:id,result:{ok:false,reason:"SIGN_IN"}}));assert.match(signIn,/href="\/risk"/);assert.doesNotMatch(signIn,new RegExp(binding.sdk_trace_id));
 const invalid=renderToStaticMarkup(React.createElement(Panel,{runId:"private-invalid",result:{ok:false,reason:"INVALID_TRACE"}}));assert.doesNotMatch(invalid,/private-invalid/);assert.match(invalid,/Enter a canonical advisory run ID/);
});
test("private lookup validates before cookies/fetch and keeps bounded uncached upstream access",async()=>{
 const boundary=compile(readFileSync(new URL("../src/features/operator-session/boundary.ts",import.meta.url),"utf8"));
 const source=readFileSync(new URL("../src/features/operator-session/server.ts",import.meta.url),"utf8").replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.lookupCookies();').replace('import { NextResponse } from "next/server";','const NextResponse={};').replace('from "./boundary"',`from "${boundary}"`);
 const server=await import(compile(source));assert.equal(typeof server.readAgentTraceBinding,"function");
 const oldFetch=globalThis.fetch,oldCookies=globalThis.lookupCookies,calls=[];
 try{
  globalThis.lookupCookies=()=>{throw new Error("must not read invalid input");};
  assert.deepEqual(await server.readAgentTraceBinding("../../private"),{ok:false,reason:"INVALID_TRACE"});
  globalThis.lookupCookies=()=>({get:()=>undefined});assert.deepEqual(await server.readAgentTraceBinding(id),{ok:false,reason:"SIGN_IN"});
  globalThis.lookupCookies=()=>({get:()=>({value:"private-session"})});globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(value));};
  assert.equal((await server.readAgentTraceBinding(id)).ok,true);assert.equal(new URL(calls[0].url).pathname,`/api/v1/observability/agent-runs/${id}/trace-binding`);assert.equal(calls[0].options.headers["X-Operator-Session"],"private-session");assert.equal(calls[0].options.cache,"no-store");assert.equal(calls[0].options.redirect,"error");assert(calls[0].options.signal instanceof AbortSignal);
  globalThis.fetch=async()=>new Response('x'.repeat(4097));assert.deepEqual(await server.readAgentTraceBinding(id),{ok:false,reason:"UNAVAILABLE"});
  globalThis.fetch=async()=>new Response(null,{status:401});assert.deepEqual(await server.readAgentTraceBinding(id),{ok:false,reason:"SIGN_IN"});
 }finally{globalThis.fetch=oldFetch;if(oldCookies===undefined)delete globalThis.lookupCookies;else globalThis.lookupCookies=oldCookies;}
});
test("actual native retained-source fixtures parse without inferring missing contexts",async()=>{
 const {parseAgentTraceLookup:parse}=await modules();
 const fixtures=JSON.parse(readFileSync(new URL("./fixtures/agent-trace-lookup.json",import.meta.url),"utf8"));
 for(const v of Object.values(fixtures))assert.deepEqual(parse(v,v.run_id),v);
 assert.equal(parse({...value,observed_at:"2026-10-11T00:00:00.000001Z",binding:{...binding,recorded_at:"2026-10-11T00:00:00.000002Z"}},id),null);
});
test("Traces page wires exact advisory identity separately from graph pagination and rejects repeated query",async()=>{
 const parser=compile(read("agent-trace-lookup.ts"));
 const panel=compile(read("agent-trace-lookup-panel.tsx").replace('from "./agent-trace-lookup"',`from "${parser}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href)),true);
 const pageSource=readFileSync(new URL("../src/app/traces/page.tsx",import.meta.url),"utf8")
  .replace(/^import .*operator-session\/server.*;$/m,'const {readAgentTraceBinding,readGraphRuns,readTracingStatus}=globalThis.lookupPageReads;')
  .replace(/^import .*trace-panels.*;$/m,'const GraphRunListPanel=()=>null;')
  .replace(/^import .*tracing-status-panel.*;$/m,'const TracingStatusPanel=()=>null;')
  .replace('"@/features/traces/agent-trace-lookup-panel"',JSON.stringify(panel));
 const calls=[],old=globalThis.lookupPageReads;
 try{
  globalThis.lookupPageReads={readGraphRuns:async(...args)=>{calls.push(["graph",...args]);return {ok:false,reason:"UNAVAILABLE"};},readTracingStatus:async()=>({ok:false,reason:"UNAVAILABLE"}),readAgentTraceBinding:async run=>{calls.push(["advisory",run]);return {ok:true,value};}};
  const {default:Page}=await import(compile(pageSource,true));
  const html=renderToStaticMarkup(await Page({searchParams:Promise.resolve({run:id})}));assert.match(html,new RegExp(binding.sdk_trace_id));assert.deepEqual(calls.filter(v=>v[0]==="advisory"),[["advisory",id]]);
  calls.length=0;const repeated=renderToStaticMarkup(await Page({searchParams:Promise.resolve({run:[id,other]})}));assert.equal(calls.some(v=>v[0]==="advisory"),false);assert.match(repeated,/Enter a canonical advisory run ID/);
  calls.length=0;await Page({searchParams:Promise.resolve({})});assert.equal(calls.some(v=>v[0]==="advisory"),false);
 }finally{if(old===undefined)delete globalThis.lookupPageReads;else globalThis.lookupPageReads=old;}
});

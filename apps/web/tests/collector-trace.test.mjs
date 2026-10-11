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
const fixture=()=>JSON.parse(readFileSync(new URL("./fixtures/collector-trace.json",import.meta.url),"utf8"));
async function modules(){
 assert(existsSync(new URL("../src/features/traces/collector-trace.ts",import.meta.url)),"Saved SDK span parser required");
 const parser=compile(read("collector-trace.ts"));
 return {...await import(parser),...await import(compile(read("collector-trace-panel.tsx").replace('from "./collector-trace"',`from "${parser}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href)),true))};
}
test("native saved spans select metadata and preserve exact nanoseconds and requested identity",async()=>{
 const {parseCollectorTrace:parse}=await modules(),v=fixture();
 assert.deepEqual(parse({...v,path:"private",spans:v.spans.map(s=>({...s,secret:"private"}))},v.trace_id),v);
 assert.equal(parse(v,"f".repeat(32)),null);
 assert.equal(parse({...v,complete_trace:true},v.trace_id),null);assert.equal(parse({...v,delivery_verified:true},v.trace_id),null);
 assert.equal(parse({...v,spans:[...v.spans,v.spans[0]]},v.trace_id),null);
 const bad=structuredClone(v);bad.spans[0].end_ns=(BigInt(bad.spans[0].start_ns)-BigInt(1)).toString();assert.equal(parse(bad,v.trace_id),null);
 const cycle=structuredClone(v),parent=cycle.spans.find(s=>s.parent_span_id===null),child=cycle.spans.find(s=>s.parent_span_id===parent.span_id);parent.parent_span_id=child.span_id;assert.equal(parse(cycle,v.trace_id),null);
 for(const field of ["ordinal","prefix_bytes"]){const corrupt=structuredClone(v);corrupt.files[0][field]=true;assert.equal(parse(corrupt,v.trace_id),null);}
});
test("rendered spans show exact durations, parent IDs, missing ancestry and no complete-delivery inference",async()=>{
 const {CollectorTracePanel:Panel}=await modules(),v=fixture();
 const html=renderToStaticMarkup(React.createElement(Panel,{traceId:v.trace_id,result:{ok:true,value:v}}));
 assert.match(html,/Saved trace steps/);assert.match(html,/Duration \(ns\)/);assert.match(html,/not proof of delivery/);assert.match(html,/<caption/);assert.equal((html.match(/scope="row"/g)||[]).length,4);assert.match(html,/name="sdk"/);assert.match(html,/html|Read saved spans again/);
 const partial={...v,spans:v.spans.filter(s=>s.parent_span_id!==null)};
 const absent=renderToStaticMarkup(React.createElement(Panel,{traceId:v.trace_id,result:{ok:true,value:partial}}));assert.match(absent,/Parent not in these saved records/);
 assert.doesNotMatch(html,/private|HEALTHY|onclick=/);
});
test("empty, malformed and sign-in states show no invented span rows",async()=>{
 const {CollectorTracePanel:Panel,parseCollectorTrace:parse}=await modules(),v=fixture();
 for(const state of ["DISABLED","UNAVAILABLE","NOT_OBSERVED"]){const value={...v,state,spans:[],files:state==="NOT_OBSERVED"?v.files:[]};assert(parse(value,v.trace_id));const html=renderToStaticMarkup(React.createElement(Panel,{traceId:v.trace_id,result:{ok:true,value}}));assert.doesNotMatch(html,/<tbody>/);assert.match(html,/role="status"/);}
 const html=renderToStaticMarkup(React.createElement(Panel,{traceId:v.trace_id,result:{ok:false,reason:"SIGN_IN"}}));assert.match(html,/href="\/risk"/);assert.doesNotMatch(html,/<tbody>/);
 assert.equal(parse({...v,state:"NOT_OBSERVED"},v.trace_id),null);
});
test("private saved-span reader validates before access and keeps bounded no-store session fetch",async()=>{
 const boundary=compile(readFileSync(new URL("../src/features/operator-session/boundary.ts",import.meta.url),"utf8"));
 const source=readFileSync(new URL("../src/features/operator-session/server.ts",import.meta.url),"utf8").replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.spanCookies();').replace('import { NextResponse } from "next/server";','const NextResponse={};').replace('from "./boundary"',`from "${boundary}"`);
 const server=await import(compile(source));assert.equal(typeof server.readCollectorTrace,"function");
 const oldFetch=globalThis.fetch,oldCookies=globalThis.spanCookies,calls=[],v=fixture();
 try{
  globalThis.spanCookies=()=>{throw new Error("invalid must not read cookies");};assert.deepEqual(await server.readCollectorTrace("0".repeat(32)),{ok:false,reason:"INVALID_TRACE"});
  globalThis.spanCookies=()=>({get:()=>undefined});assert.deepEqual(await server.readCollectorTrace(v.trace_id),{ok:false,reason:"SIGN_IN"});
  globalThis.spanCookies=()=>({get:()=>({value:"private-session"})});globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(v));};assert.equal((await server.readCollectorTrace(v.trace_id)).ok,true);
  assert.equal(new URL(calls[0].url).pathname,`/api/v1/observability/traces/${v.trace_id}`);assert.equal(calls[0].options.headers["X-Operator-Session"],"private-session");assert.equal(calls[0].options.cache,"no-store");assert.equal(calls[0].options.redirect,"error");assert(calls[0].options.signal instanceof AbortSignal);
  globalThis.fetch=async()=>new Response('x'.repeat(262145));assert.deepEqual(await server.readCollectorTrace(v.trace_id),{ok:false,reason:"UNAVAILABLE"});
 }finally{globalThis.fetch=oldFetch;if(oldCookies===undefined)delete globalThis.spanCookies;else globalThis.spanCookies=oldCookies;}
});
test("Traces page requests exact SDK identity and rejects repeated SDK query before read",async()=>{
 const parser=compile(read("collector-trace.ts")),panel=compile(read("collector-trace-panel.tsx").replace('from "./collector-trace"',`from "${parser}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href)),true);
 const source=readFileSync(new URL("../src/app/traces/page.tsx",import.meta.url),"utf8")
 .replace(/^import .*operator-session\/server.*;$/m,'const {readCollectorTrace,readAgentTraceBinding,readGraphRuns,readTracingStatus}=globalThis.sdkPageReads;')
 .replace(/^import .*trace-panels.*;$/m,'const GraphRunListPanel=()=>null;')
 .replace(/^import .*tracing-status-panel.*;$/m,'const TracingStatusPanel=()=>null;')
 .replace(/^import .*agent-trace-lookup-panel.*;$/m,'const AgentTraceLookupPanel=()=>null;')
 .replace('"@/features/traces/collector-trace-panel"',JSON.stringify(panel));
 const v=fixture(),calls=[],old=globalThis.sdkPageReads;
 try{
  globalThis.sdkPageReads={readCollectorTrace:async id=>{calls.push(id);return {ok:true,value:v};},readGraphRuns:async()=>({ok:false,reason:"UNAVAILABLE"}),readTracingStatus:async()=>({ok:false,reason:"UNAVAILABLE"}),readAgentTraceBinding:async()=>({ok:false,reason:"UNAVAILABLE"})};
  const {default:Page}=await import(compile(source,true));
  assert.match(renderToStaticMarkup(await Page({searchParams:Promise.resolve({sdk:v.trace_id})})),/Duration \(ns\)/);assert.deepEqual(calls,[v.trace_id]);
  calls.length=0;const repeated=renderToStaticMarkup(await Page({searchParams:Promise.resolve({sdk:[v.trace_id,"f".repeat(32)]})}));assert.equal(calls.length,0);assert.match(repeated,/Enter a nonzero/);
  await Page({searchParams:Promise.resolve({})});assert.equal(calls.length,0);
 }finally{if(old===undefined)delete globalThis.sdkPageReads;else globalThis.sdkPageReads=old;}
});
test("native Next loading/error surfaces infer no outcomes, omit raw errors and use installed retry callback",async()=>{
 const loading=await import(compile(readFileSync(new URL("../src/app/traces/loading.tsx",import.meta.url),"utf8"),true));
 assert.match(renderToStaticMarkup(React.createElement(loading.default)),/aria-busy="true"/);assert.match(renderToStaticMarkup(React.createElement(loading.default)),/No outcomes are inferred/);
 const error=await import(compile(readFileSync(new URL("../src/app/traces/error.tsx",import.meta.url),"utf8"),true));
 let reads=0;const tree=error.default({retry:()=>reads++,error:new Error("private-error")});
 const html=renderToStaticMarkup(tree);assert.doesNotMatch(html,/private-error/);assert.match(html,/role="alert"/);
 const button=tree.props.children.find(child=>child?.type==="button");button.props.onClick();assert.equal(reads,1);
});
test("advisory context links the original SDK namespace without prefetching span reads",async()=>{
 const parser=compile(read("agent-trace-lookup.ts")),panel=await import(compile(read("agent-trace-lookup-panel.tsx").replace('from "./agent-trace-lookup"',`from "${parser}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href)),true));
 const v=JSON.parse(readFileSync(new URL("./fixtures/agent-trace-lookup.json",import.meta.url),"utf8")).available;
 const html=renderToStaticMarkup(React.createElement(panel.AgentTraceLookupPanel,{runId:v.run_id,result:{ok:true,value:v}}));assert.match(html,new RegExp(`href="/traces\\?sdk=${v.binding.sdk_trace_id}"`));assert.match(html,/Inspect saved trace steps/);
 assert.match(read("agent-trace-lookup-panel.tsx"),/href=\{`\/traces\?sdk=\$\{binding.sdk_trace_id\}`\} prefetch=\{false\}/);
});

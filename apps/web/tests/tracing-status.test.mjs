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
const read=name=>readFileSync(new URL(`../src/features/traces/${name}`,import.meta.url),"utf8");
async function modules(){
 assert(existsSync(new URL("../src/features/traces/tracing-status.ts",import.meta.url)),"Native trace diagnostics parser must exist");
 const parser=compile(read("tracing-status.ts"));
 return {...await import(parser),...await import(compile(read("tracing-status-panel.tsx").replace('from "./tracing-status"',`from "${parser}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href)),true))};
}
const fixtures=()=>JSON.parse(readFileSync(new URL("./fixtures/tracing-status.json",import.meta.url),"utf8"));
const render=(Component,result)=>renderToStaticMarkup(React.createElement(Component,{result}));
test("native SDK fixture preserves original finished inventory and strips unrelated private fields",async()=>{
 const {parseTracingStatus:parse}=await modules();
 for(const value of Object.values(fixtures()))assert.deepEqual(parse({...value,credential:"private",endpoint:"private"}),value);
});
test("rejects inconsistent inventory, misleading authority, dates and nonnative liveness",async()=>{
 const {parseTracingStatus:parse}=await modules(),v=fixtures().running;
 for(const value of [{...v,observed_spans:v.observed_spans+1},{...v,acknowledged_batches:0},{...v,worker_alive:false},{...v,inflight_spans:33},{...v,pending_spans:513},{...v,dropped_spans:-1},{...v,instrumentation_errors:true},{...v,observed_spans:Number.MAX_SAFE_INTEGER+1},{...v,queue_capacity:999},{...v,delivery_verified:true},{...v,durable:true},{...v,runtime_id:"private"},{...v,sdk_version:"private"},{...v,exporter_kind:"REMOTE"},{...v,observed_at:"2026-02-30T00:00:00Z"},{...v,observed_at:"2026-10-11T01:00:00+01:00"}])assert.equal(parse(value),null);
});
test("disabled and unavailable snapshots contain no inferred zero counts",async()=>{
 const {parseTracingStatus:parse,TracingStatusPanel:Panel}=await modules();
 for(const state of ["DISABLED","UNAVAILABLE"]){
  const v={...fixtures().disabled,runtime_state:state};assert.deepEqual(parse(v),v);assert.equal(parse({...v,observed_spans:0}),null);
  const html=render(Panel,{ok:true,value:v});assert.doesNotMatch(html,/<tbody>/);assert.match(html,/No counts/);assert.match(html,/role="status"/);
 }
});
test("rendered trace queue shows explicit per-runtime outcomes, custody and refresh",async()=>{
 const {TracingStatusPanel:Panel}=await modules(),v=fixtures().running;
 const html=render(Panel,{ok:true,value:{...v,credential:"private",endpoint:"private"}});
 assert.match(html,/Trace export diagnostics/);assert.match(html,/Exporter acknowledged/);assert.match(html,/Unconfirmed/);assert.match(html,/Dropped/);assert.match(html,/In flight/);assert.match(html,/not proof of durable delivery/);assert.match(html,/do not survive a restart/);assert.match(html,/Original worker observed alive/);
 assert.match(html,/<time/);assert.match(html,/<form(?=[^>]*method="get")(?=[^>]*action="\/traces")[^>]*>/);assert.equal((html.match(/scope="row"/g)||[]).length,8);assert.doesNotMatch(html,/private|HEALTHY|onclick=/);
});
test("failure and stopping retain exact outcomes without implying worker replacement",async()=>{
 const {parseTracingStatus:parse,TracingStatusPanel:Panel}=await modules(),v=fixtures().running;
 const stopping={...v,runtime_state:"STOPPING",observed_spans:34,acknowledged_spans:2,acknowledged_batches:1,inflight_spans:32};
 assert(parse(stopping));assert.equal(parse({...stopping,worker_alive:false}),null);
 const failed={...v,runtime_state:"FAILED",observed_spans:3,acknowledged_spans:0,acknowledged_batches:0,unconfirmed_spans:2,failed_batches:1,dropped_spans:1};
 for(const worker_alive of [true,false])assert(parse({...failed,worker_alive}));
 assert.equal(parse({...failed,pending_spans:1,observed_spans:4}),null);
 assert.match(render(Panel,{ok:true,value:stopping}),/original worker may still be finishing/);
 assert.match(render(Panel,{ok:true,value:failed}),/Export work failed/);
 assert.match(render(Panel,{ok:true,value:fixtures().stopped}),/Original worker observed terminated/);
});
test("sign-in and malformed results withhold diagnostics and route to Risk Center",async()=>{
 const {TracingStatusPanel:Panel}=await modules();
 const html=render(Panel,{ok:false,reason:"SIGN_IN"});assert.match(html,/href="\/risk"/);assert.doesNotMatch(html,/<tbody>/);
 const bad=render(Panel,{ok:true,value:{...fixtures().running,observed_spans:999}});assert.match(bad,/UNAVAILABLE/);assert.doesNotMatch(bad,/999|<tbody>/);
});

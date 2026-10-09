import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const require=createRequire(import.meta.url);
function compile(source) {return ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;}
const parser=`data:text/javascript;base64,${Buffer.from(compile(readFileSync(new URL('../src/features/traces/graph-traces.ts',import.meta.url),'utf8'))).toString('base64')}`;
globalThis.traceTestLink=({children,prefetch,...props})=>{void prefetch;return React.createElement('a',props,children);};
const source=readFileSync(new URL('../src/features/traces/trace-panels.tsx',import.meta.url),'utf8').replace('import Link from "next/link";','const Link = globalThis.traceTestLink;').replace('from "./graph-traces"',`from "${parser}"`);
const {GraphRunListPanel,GraphTracePanel}=await import(`data:text/javascript;base64,${Buffer.from(compile(source).replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))).toString('base64')}`);
delete globalThis.traceTestLink;
const identity={run_id:'run_1',agent_id:'A01',agent_version:'0.4.0',task_id:'review',scope:'PAPER',status:'COMPLETED',context_ref:'agent-context:'+ 'a'.repeat(64),manifest_ref:'agent-manifest:'+'b'.repeat(64),trace_id:'graph-trace:'+'c'.repeat(64),context_observed_at:'2026-10-09T12:00:00Z',context_valid_until:'2026-10-09T12:00:05Z'};
const page={schema_version:'1.0',mode:'PAPER',archival_only:true,head_hash:'a'.repeat(64),total:1,after_run_id:'',remaining:0,next_after:null,records:[identity]};
const trace={schema_version:'1.0',mode:'PAPER',archival_only:true,requires_escalation:true,...identity,input_refs:['evidence:test'],output_trace_id:null,output_sha256:'f'.repeat(64),events:['CREATED','RUNNING','COMPLETED'].map((status,index)=>({sequence:index+1,status,record_hash:'d'.repeat(64),event_hash:'e'.repeat(64),observed_at:index===2?'2026-10-09T12:00:01.250Z':'2026-10-09T12:00:01Z'})),timing_state:'AVAILABLE',elapsed_seconds:'0.25'};
test('list opens actual saved runs and handles empty, sign-in, changed and damaged data',()=>{
  const html=renderToStaticMarkup(React.createElement(GraphRunListPanel,{result:{ok:true,value:{...page,credential:'private',records:[{...identity,output_json:'private'}]}}}));
  assert.match(html,/href="\/traces\/run_1"/);assert.match(html,/Context observed/);assert.doesNotMatch(html,/private/);
  const empty=renderToStaticMarkup(React.createElement(GraphRunListPanel,{result:{ok:true,value:{...page,total:0,records:[]}}}));
  assert.match(empty,/No agent executions saved/);
  const last=renderToStaticMarkup(React.createElement(GraphRunListPanel,{after:'run_1',result:{ok:true,value:{...page,after_run_id:'run_1',records:[]}}}));
  assert.match(last,/No more executions on this page/);
  const changed=renderToStaticMarkup(React.createElement(GraphRunListPanel,{after:'other',result:{ok:true,value:page}}));
  assert.doesNotMatch(changed,/<tbody>/);
  for(const reason of ['SIGN_IN','CHANGED','UNAVAILABLE','INVALID_PAGE']) {
    const output=renderToStaticMarkup(React.createElement(GraphRunListPanel,{result:{ok:false,reason}}));
    assert.match(output,/role="status"/);assert.doesNotMatch(output,/<tbody>/);
    assert.match(output,reason==='SIGN_IN'?/href="\/settings"/:/href="\/traces"/);
  }
});
test('timeline keeps observation and reservation timing separate with metadata only',()=>{
  const html=renderToStaticMarkup(React.createElement(GraphTracePanel,{runId:'run_1',result:{ok:true,value:{...trace,checkpoint:{prompt:'private'},events:trace.events.map(row=>({...row,prompt:'private'}))}}}));
  assert.equal((html.match(/scope="row"/g)||[]).length,3);assert.match(html,/0.25 seconds/);assert.match(html,/reservation interval/);assert.match(html,/Context observed/);assert.doesNotMatch(html,/private|TRACE SERVICE PENDING/);
  const old=renderToStaticMarkup(React.createElement(GraphTracePanel,{runId:'run_1',result:{ok:true,value:{...trace,timing_state:'UNAVAILABLE',elapsed_seconds:null,events:trace.events.map(row=>({...row,observed_at:null}))}}}));
  assert.match(old,/not recorded/);assert.doesNotMatch(old,/0.25 seconds/);
  for(const result of [{ok:false,reason:'NOT_FOUND'},{ok:false,reason:'SIGN_IN'},{ok:true,value:{...trace,run_id:'wrong'}}]) {
    const output=renderToStaticMarkup(React.createElement(GraphTracePanel,{runId:'run_1',result}));
    assert.match(output,/role="status"/);assert.doesNotMatch(output,/<tbody>/);
  }
});

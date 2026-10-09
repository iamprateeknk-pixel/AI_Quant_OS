import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/traces/graph-traces.ts',import.meta.url),'utf8');
const compiled = ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {parseGraphTrace,parseGraphRunPage}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const identity={run_id:'run_1',agent_id:'A01',agent_version:'0.4.0',task_id:'review',scope:'PAPER',status:'COMPLETED',context_ref:'agent-context:'+ 'a'.repeat(64),manifest_ref:'agent-manifest:'+'b'.repeat(64),trace_id:'graph-trace:'+'c'.repeat(64),context_observed_at:'2026-10-09T12:00:00Z',context_valid_until:'2026-10-09T12:00:05Z'};
const events=['CREATED','RUNNING','COMPLETED'].map((status,index)=>({sequence:index+1,status,record_hash:'d'.repeat(64),event_hash:'e'.repeat(64),observed_at:index===2?'2026-10-09T12:00:01.250000Z':'2026-10-09T12:00:01Z'}));
const trace={schema_version:'1.0',mode:'PAPER',archival_only:true,requires_escalation:true,...identity,input_refs:['evidence:test'],output_trace_id:'original-envelope-trace',output_sha256:'f'.repeat(64),events,timing_state:'AVAILABLE',elapsed_seconds:'0.25'};
const page={schema_version:'1.0',mode:'PAPER',archival_only:true,head_hash:'a'.repeat(64),total:1,after_run_id:'',remaining:0,next_after:null,records:[identity]};

test('projects only original trace and page metadata and binds requested run',()=>{
  assert.deepEqual(parseGraphTrace({...trace,credential:'private',checkpoint:{prompt:'private'},events:events.map(row=>({...row,prompt:'private'}))},'run_1'),trace);
  assert.deepEqual(parseGraphRunPage({...page,session_token:'private',records:[{...identity,output_json:'private'}]}),page);
  assert.equal(parseGraphRunPage(page,'other'),null);
  assert.equal(parseGraphRunPage(page,'','b'.repeat(64)),null);
  assert.equal(parseGraphTrace(trace,'another-run'),null);
});
test('verifies elapsed time without losing microsecond precision',()=>{
  const precise={...trace,events:events.map((row,index)=>({...row,observed_at:index===2?'2026-10-09T12:00:01.000001Z':'2026-10-09T12:00:01Z'})),elapsed_seconds:'0.000001'};
  assert.deepEqual(parseGraphTrace(precise),precise);
  assert.equal(parseGraphTrace({...precise,elapsed_seconds:'0'}),null);
});
test('keeps legacy, partial and regressing timing explicit',()=>{
  for(const [state,times] of [['UNAVAILABLE',[null,null,null]],['PARTIAL',[null,events[1].observed_at,events[2].observed_at]],['CLOCK_REGRESSION',[events[0].observed_at,events[1].observed_at,'2026-10-09T12:00:00Z']]]) {
    const value={...trace,timing_state:state,elapsed_seconds:null,events:events.map((row,index)=>({...row,observed_at:times[index]}))};
    assert.deepEqual(parseGraphTrace(value),value);
    assert.equal(parseGraphTrace({...value,elapsed_seconds:'0'}),null);
  }
});
test('rejects forged states, event identities, times and output claims',()=>{
  for(const value of [{...trace,mode:'LIVE'},{...trace,status:'FAILED'},{...trace,elapsed_seconds:'10'},{...trace,output_sha256:null},{...trace,events:events.slice(1)},{...trace,events:events.map(row=>({...row,sequence:1}))},{...trace,events:events.map(row=>({...row,observed_at:'2026-02-30T00:00:00Z'}))},{...trace,events:events.map(row=>({...row,observed_at:'2026-10-09T12:00:01+05:30'}))},{...trace,input_refs:Array(129).fill('x')},{...trace,context_valid_until:trace.context_observed_at}]) assert.equal(parseGraphTrace(value),null);
});
test('rejects omitted, duplicate, unsorted, or inconsistent page records',()=>{
  for(const value of [{...page,total:2},{...page,records:[]},{...page,remaining:1,next_after:null},{...page,total:2,records:[identity,identity]},{...page,total:2,records:[{...identity,run_id:'z'},identity]},{...page,after_run_id:'run_2'},{...page,next_after:'https://invalid.example'},{...page,records:[{...identity,run_id:'../private'}]}]) assert.equal(parseGraphRunPage(value),null);
});

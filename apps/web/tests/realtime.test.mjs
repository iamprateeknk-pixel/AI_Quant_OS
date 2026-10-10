import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import ts from 'typescript';
const path=new URL('../src/features/realtime/frames.ts',import.meta.url);
const now='2026-10-10T00:00:00.000Z';
const frame=()=>({schema_version:'1.0',mode:'PAPER',epoch:'12345678-1234-4123-8123-123456789abc',sequence:1,captured_at:now,valid_until:'2026-10-10T00:00:03Z',safety_state:'HALT',order_count:4,world:{schema_version:'1.0',source:'PAPER_SIMULATED',world_state_version:'world:'+'a'.repeat(64),as_of:now,valid_until:'2026-10-10T00:00:05Z',status:'EMPTY',instruments:[{instrument_id:'EURUSD',quote_status:'MISSING',history_status:'MISSING',bar_count:0,regime:'UNKNOWN'}]}});
async function module(){assert(existsSync(path),'Realtime parser must exist');return import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);}
test('realtime projection retains original authority/time and strips secrets',async()=>{
 const m=await module(),raw=frame();raw.private_token='fixture-secret';raw.world.password='fixture-secret';
 const parsed=m.parseRealtimeFrame(raw,Date.parse(now));assert(parsed);assert.equal(parsed.safety_state,'HALT');assert.equal(parsed.order_count,4);assert(!JSON.stringify(parsed).includes('fixture-secret'));
 assert.equal(m.frameCurrent(parsed,Date.parse(now)+3000),false);
});
test('realtime parser rejects expired/future/invalid leases and unsafe numbers',async()=>{
 const m=await module();for(const patch of [{sequence:true},{sequence:2**53},{order_count:-1},{safety_state:'APPROVED'},{mode:'LIVE'},{captured_at:'2026-10-10T00:00:01Z'},{valid_until:'2026-10-10T00:00:04Z'}]) assert.equal(m.parseRealtimeFrame({...frame(),...patch},Date.parse(now)),null);
 assert.equal(m.parseRealtimeFrame(frame(),Date.parse(now)+3000),null);
});
test('sequence regressions and unexpected epochs cannot replace accepted state',async()=>{
 const m=await module(),first=m.parseRealtimeFrame(frame(),Date.parse(now));assert(first);
 assert.equal(m.acceptRealtimeFrame(first,first,false),false);
 const next={...first,sequence:2};assert.equal(m.acceptRealtimeFrame(first,next,false),true);
 const epoch={...next,epoch:'12345678-1234-4123-8123-123456789abd'};
 assert.equal(m.acceptRealtimeFrame(first,epoch,false),false);assert.equal(m.acceptRealtimeFrame(first,epoch,true),true);
 assert.equal(m.acceptRealtimeFrame(null,first,false),false);assert.equal(m.acceptRealtimeFrame(null,first,true),true);
});
test('stream proxy strips private fields, bounds events and releases upstream',async()=>{
 const m=await module(),proxyPath=new URL('../src/features/realtime/stream-proxy.ts',import.meta.url);assert(existsSync(proxyPath),'Realtime stream proxy must exist');
 const framesUrl=`data:text/javascript;base64,${Buffer.from(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
 const source=readFileSync(proxyPath,'utf8').replace('from "./frames"',`from "${framesUrl}"`);
 const proxy=await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);
 const raw=frame();raw.private_token='fixture-private';
 const response=new Response(`id: ${raw.epoch}:1\nevent: reset\ndata: ${JSON.stringify(raw)}\n\n`,{headers:{'Content-Type':'text/event-stream'}});
 const output=await proxy.proxyRealtimeResponse(response,new AbortController().signal,()=>Date.parse(now)).text();assert(output.includes('event: reset'));assert(!output.includes('fixture-private'));
 const wrong=new Response('x'.repeat(33000),{headers:{'Content-Type':'text/event-stream'}});
 await assert.rejects(proxy.proxyRealtimeResponse(wrong,new AbortController().signal,()=>Date.parse(now)).text());
 let cancelled=false;
 const invalid=new Response(new ReadableStream({cancel(){cancelled=true;}}),{headers:{'Content-Type':'application/json'}});
 assert.throws(()=>proxy.proxyRealtimeResponse(invalid,new AbortController().signal));assert.equal(cancelled,true);
});
test('actual backend snapshot fixture is accepted as simulated paper evidence',async()=>{
 const m=await module(),fixture=JSON.parse(readFileSync(new URL('./fixtures/realtime-snapshot.json',import.meta.url),'utf8'));
 const result=m.parseRealtimeFrame(fixture,Date.parse(fixture.captured_at));assert(result);assert.equal(result.instruments.length,fixture.world.instruments.length);assert.equal(result.order_count,0);assert.equal(result.safety_state,'NORMAL');
});

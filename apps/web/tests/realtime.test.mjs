import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import ts from 'typescript';
const path=new URL('../src/features/realtime/frames.ts',import.meta.url);
const now='2026-10-10T00:00:00.000Z';
const frame=()=>({schema_version:'1.0',mode:'PAPER',epoch:'12345678-1234-4123-8123-123456789abc',sequence:1,captured_at:now,valid_until:'2026-10-10T00:00:03Z',safety_state:'HALT',order_count:4,world:{schema_version:'1.0',source:'PAPER_SIMULATED',world_state_version:'world:'+'a'.repeat(64),as_of:now,valid_until:'2026-10-10T00:00:05Z',status:'EMPTY',instruments:[{instrument_id:'EURUSD',quote_status:'MISSING',history_status:'MISSING',bar_count:0,regime:'UNKNOWN',mid_price:null,spread_bps:null,return_bps:null,realized_volatility_bps:null}],currency_twins:[{currency:'EUR',instrument_ids:['EURUSD'],fresh_quote_count:0,macro_available:false},{currency:'USD',instrument_ids:['EURUSD'],fresh_quote_count:0,macro_available:false}]}});
async function module(){assert(existsSync(path),'Realtime parser must exist');return import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);}
async function proxyModule(){
 const framesUrl=`data:text/javascript;base64,${Buffer.from(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
 const source=readFileSync(new URL('../src/features/realtime/stream-proxy.ts',import.meta.url),'utf8').replace('from "./frames"',`from "${framesUrl}"`);
 return import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);
}
test('realtime projection retains original authority/time and strips secrets',async()=>{
 const m=await module(),raw=frame();raw.private_token='fixture-secret';raw.world.password='fixture-secret';
 const parsed=m.parseRealtimeFrame(raw,Date.parse(now));assert(parsed);assert.equal(parsed.safety_state,'HALT');assert.equal(parsed.order_count,4);assert(!JSON.stringify(parsed).includes('fixture-secret'));
 assert.equal(m.frameCurrent(parsed,Date.parse(now)+3000),false);
});
test('realtime parser rejects expired/future/invalid leases and unsafe numbers',async()=>{
 const m=await module();for(const patch of [{sequence:true},{sequence:2**53},{order_count:-1},{safety_state:'APPROVED'},{mode:'LIVE'},{captured_at:'2026-10-10T00:00:01Z'},{valid_until:'2026-10-10T00:00:04Z'}]) assert.equal(m.parseRealtimeFrame({...frame(),...patch},Date.parse(now)),null);
 assert.equal(m.parseRealtimeFrame(frame(),Date.parse(now)+3000),null);
});
test('realtime frame allows only a bounded 25ms same-host future clock skew',async()=>{
 const m=await module(),raw=frame(),parsed=m.parseRealtimeFrame(raw,Date.parse(now)-25);assert(parsed);
 assert.equal(m.parseRealtimeFrame(raw,Date.parse(now)-26),null);
 assert.equal(m.frameCurrent(parsed,Date.parse(raw.valid_until)),false);
});
test('full snapshots recover sequence gaps while regressions and unexpected epochs are rejected',async()=>{
 const m=await module(),first=m.parseRealtimeFrame(frame(),Date.parse(now));assert(first);
 assert.equal(m.acceptRealtimeFrame(first,first,false),false);
 // Sequence numbers are global to the API instance, so other streams can
 // consume numbers between these complete snapshots. A later full snapshot
 // is sufficient to restore the current view without replaying missing IDs.
 const next={...first,sequence:7};assert.equal(m.acceptRealtimeFrame(first,next,false),true);
 assert.equal(m.acceptRealtimeFrame(next,{...next,sequence:6},false),false);
 const epoch={...next,epoch:'12345678-1234-4123-8123-123456789abd'};
 assert.equal(m.acceptRealtimeFrame(first,epoch,false),false);assert.equal(m.acceptRealtimeFrame(first,epoch,true),true);
 assert.equal(m.acceptRealtimeFrame(null,first,false),false);assert.equal(m.acceptRealtimeFrame(null,first,true),true);
});
test('stream proxy strips private fields, bounds events and releases upstream',async()=>{
  const proxyPath=new URL('../src/features/realtime/stream-proxy.ts',import.meta.url);assert(existsSync(proxyPath),'Realtime stream proxy must exist');
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
test('world-state features and coverage retain exact source values and reject incoherent coverage',async()=>{
 const m=await module(),raw=frame();raw.world.instruments[0].return_bps='-12.345678901234567890';raw.world.instruments[0].spread_bps='0.00000000000000000000001';
 const parsed=m.parseRealtimeFrame(raw,Date.parse(now));assert(parsed);assert.equal(parsed.instruments[0].return_bps,'-12.345678901234567890');assert.equal(parsed.instruments[0].spread_bps,raw.world.instruments[0].spread_bps);assert.deepEqual(parsed.currency_twins,raw.world.currency_twins);
 const invalid=structuredClone(raw);invalid.world.currency_twins[0].fresh_quote_count=1;assert.equal(m.parseRealtimeFrame(invalid,Date.parse(now)),null);
 const unsafe=structuredClone(raw);unsafe.world.instruments[0].mid_price='Infinity';assert.equal(m.parseRealtimeFrame(unsafe,Date.parse(now)),null);
});
test('populated protected backend features survive proxy validation without rounding',async()=>{
 const m=await module(),fixture=JSON.parse(readFileSync(new URL('./fixtures/realtime-world.json',import.meta.url),'utf8'));
 const proxy=await proxyModule(),response=new Response(`id: ${fixture.epoch}:${fixture.sequence}\nevent: reset\ndata: ${JSON.stringify(fixture)}\n\n`,{headers:{'Content-Type':'text/event-stream'}});
 const output=await proxy.proxyRealtimeResponse(response,new AbortController().signal,()=>Date.parse(fixture.captured_at)).text();
 const forwarded=JSON.parse(output.split('\n').find(line=>line.startsWith('data: ')).slice(6));
 const parsed=m.parseRealtimeFrame(forwarded,Date.parse(fixture.captured_at));assert(parsed);
 const euro=parsed.instruments.find(row=>row.instrument_id==='EURUSD'),source=fixture.world.instruments.find(row=>row.instrument_id==='EURUSD');
 assert.equal(euro.return_bps,source.return_bps);assert.equal(euro.realized_volatility_bps,source.realized_volatility_bps);assert.deepEqual(parsed.currency_twins,fixture.world.currency_twins);
});

test('stream proxy identifies stale frame leases and mismatched event IDs without echoing payloads',async()=>{
 const proxy=await proxyModule(),raw=frame();
 const event=(id)=>new Response(`id: ${id}\nevent: reset\ndata: ${JSON.stringify(raw)}\n\n`,{headers:{'Content-Type':'text/event-stream'}});
 const tolerated=await proxy.proxyRealtimeResponse(event(`${raw.epoch}:1`),new AbortController().signal,()=>Date.parse(now)-8).text();assert(tolerated.includes('event: reset'));
 await assert.rejects(proxy.proxyRealtimeResponse(event(`${raw.epoch}:1`),new AbortController().signal,()=>Date.parse(now)+3001).text(),/Realtime frame expired by 1ms/);
 const future={...raw,captured_at:'2026-10-10T00:00:00.001Z'};
 await assert.rejects(proxy.proxyRealtimeResponse(new Response(`id: ${future.epoch}:1\nevent: reset\ndata: ${JSON.stringify(future)}\n\n`,{headers:{'Content-Type':'text/event-stream'}}),new AbortController().signal,()=>Date.parse(now)).text(),/Realtime frame timestamp is in the future by 1ms/);
 await assert.rejects(proxy.proxyRealtimeResponse(event(`${raw.epoch}:2`),new AbortController().signal,()=>Date.parse(now)).text(),/Realtime event ID mismatch/);
});

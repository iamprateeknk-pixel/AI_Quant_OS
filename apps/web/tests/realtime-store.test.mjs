import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import ts from 'typescript';
const compile=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
const frames=compile(readFileSync(new URL('../src/features/realtime/frames.ts',import.meta.url),'utf8'));
const path=new URL('../src/features/realtime/store.ts',import.meta.url);
async function module(){assert(existsSync(path),'Shared realtime store must exist');return import(compile(readFileSync(path,'utf8').replace('from "./frames"',`from "${frames}"`)));}
function fixture(){
 const sources=[],timers=new Map();let now=Date.parse('2026-10-10T00:00:00Z'),nextTimer=0;
 class Source{
  listeners=new Map();onerror=null;closed=false;
  addEventListener(type,callback){this.listeners.set(type,callback);}
  removeEventListener(type){this.listeners.delete(type);}
  close(){this.closed=true;}
  emit(type,raw){this.listeners.get(type)?.({data:JSON.stringify(raw),lastEventId:`${raw.epoch}:${raw.sequence}`});}
 }
 return {sources,timers,options:{connect(){const source=new Source();sources.push(source);return source;},clock:()=>now,schedule(callback,delay){const id=++nextTimer;timers.set(id,{callback,at:now+delay});return id;},cancel(id){timers.delete(id);}},advance(ms){now+=ms;for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.callback();}},frame(sequence=1){return {...JSON.parse(readFileSync(new URL('./fixtures/realtime-snapshot.json',import.meta.url),'utf8')),sequence,captured_at:new Date(now).toISOString(),valid_until:new Date(now+3000).toISOString(),world:{...JSON.parse(readFileSync(new URL('./fixtures/realtime-snapshot.json',import.meta.url),'utf8')).world,as_of:new Date(now).toISOString(),valid_until:new Date(now+5000).toISOString()}};}};
}
test('all subscribers share one channel and original expiry clears current state',async()=>{
 const m=await module(),f=fixture(),store=m.createRealtimeStore(f.options);let notifications=0;
 assert.deepEqual(store.serverSnapshot(),{state:'CONNECTING',frame:null});assert.equal(f.sources.length,0);
 const a=store.subscribe(()=>notifications++),b=store.subscribe(()=>notifications++);assert.equal(f.sources.length,1);
 f.sources[0].emit('reset',f.frame());assert.equal(store.snapshot().state,'CURRENT');const current=store.snapshot();assert.equal(store.snapshot(),current);
 f.advance(3000);assert.equal(store.snapshot().state,'STALE');assert.equal(store.snapshot().frame.sequence,1);assert(notifications>0);
 a();assert.equal(f.sources[0].closed,false);b();assert.equal(f.sources[0].closed,true);assert.equal(f.timers.size,0);
});
test('failed private access retries with one source and old callbacks cannot reauthorize data',async()=>{
 const m=await module(),f=fixture(),store=m.createRealtimeStore(f.options),unsubscribe=store.subscribe(()=>{}),old=f.sources[0];
 const late=old.listeners.get('reset');old.onerror(new Event('error'));assert.equal(store.snapshot().state,'DISCONNECTED');assert(old.closed);
 f.advance(2000);assert.equal(f.sources.length,2);assert.equal(f.sources[1].closed,false);
 late({data:JSON.stringify(f.frame()),lastEventId:`${f.frame().epoch}:1`});assert.equal(store.snapshot().state,'DISCONNECTED');
 f.sources[1].emit('reset',f.frame());assert.equal(store.snapshot().state,'CURRENT');unsubscribe();assert.equal(f.timers.size,0);
});
test('regressions and epoch changes are withheld without replacing accepted evidence',async()=>{
 const m=await module(),f=fixture(),store=m.createRealtimeStore(f.options),unsubscribe=store.subscribe(()=>{});
 f.sources[0].emit('reset',f.frame(2));f.sources[0].emit('snapshot',f.frame(1));assert.equal(store.snapshot().state,'WITHHELD');assert.equal(store.snapshot().frame.sequence,2);
 f.sources[0].emit('snapshot',f.frame(3));assert.equal(store.snapshot().state,'CURRENT');
 const epoch={...f.frame(4),epoch:'12345678-1234-4123-8123-123456789abc'};f.sources[0].emit('snapshot',epoch);assert.equal(store.snapshot().state,'WITHHELD');unsubscribe();
});
test('access failures back off to a bounded delay and last unsubscribe cancels retry',async()=>{
 const m=await module(),f=fixture(),store=m.createRealtimeStore(f.options),unsubscribe=store.subscribe(()=>{});
 for(const delay of [2000,4000,8000,16000,30000,30000]){
  f.sources.at(-1).onerror(new Event('error'));assert.equal(f.timers.size,1);
  const before=f.sources.length;f.advance(delay-1);assert.equal(f.sources.length,before);f.advance(1);assert.equal(f.sources.length,before+1);
  assert.equal(f.sources.filter(source=>!source.closed).length,1);
 }
 f.sources.at(-1).onerror(new Event('error'));unsubscribe();assert.equal(f.timers.size,0);f.advance(30000);assert.equal(f.sources.length,7);
});

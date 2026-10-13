import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const base=new URL('../src/features/realtime/',import.meta.url);
const moduleUrl=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
const frames=moduleUrl(readFileSync(new URL('frames.ts',base),'utf8'));
const source=readFileSync(new URL('store.ts',base),'utf8').replace('from "./frames"',`from "${frames}"`);
const {createRealtimeStore}=await import(moduleUrl(source));

function harness(authorize){
 const scheduled=[],cancelled=[];let connections=0,closed=0;
 const store=createRealtimeStore({authorize,connect:()=>{connections++;return {addEventListener(){},removeEventListener(){},close(){closed++},onerror:null};},clock:()=>Date.now(),schedule:(callback,delay)=>{scheduled.push({callback,delay});return scheduled.length;},cancel:id=>cancelled.push(id)});
 return {store,scheduled,cancelled,connections:()=>connections,closed:()=>closed};
}

test('unauthenticated realtime subscription does not open an EventSource and backs off',async()=>{
 const h=harness(async()=>false),unsubscribe=h.store.subscribe(()=>{});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.connections(),0);assert.equal(h.store.snapshot().state,'DISCONNECTED');
 assert.deepEqual(h.scheduled.map(item=>item.delay),[2000]);
 unsubscribe();assert.deepEqual(h.cancelled,[1]);
});

test('authorized realtime subscription opens one source after session validation',async()=>{
 const h=harness(async()=>true),unsubscribe=h.store.subscribe(()=>{});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.connections(),1);assert.equal(h.store.snapshot().state,'CONNECTING');
 unsubscribe();assert.equal(h.closed(),1);
});

test('unsubscribe invalidates a pending authorization check before it can connect',async()=>{
 let authorize;
 const h=harness(()=>new Promise(resolve=>{authorize=resolve;})),unsubscribe=h.store.subscribe(()=>{});
 await Promise.resolve();unsubscribe();authorize(true);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.connections(),0);assert.equal(h.scheduled.length,0);assert.equal(h.store.snapshot().state,'DISCONNECTED');
});

test('authorization failures retry without opening the protected stream',async()=>{
 const h=harness(async()=>{throw new Error('probe unavailable');}),unsubscribe=h.store.subscribe(()=>{});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.connections(),0);assert.equal(h.store.snapshot().state,'DISCONNECTED');
 assert.deepEqual(h.scheduled.map(item=>item.delay),[2000]);unsubscribe();
});

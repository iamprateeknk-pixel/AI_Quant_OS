import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const compile=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
const frames=compile(readFileSync(new URL('../src/features/realtime/frames.ts',import.meta.url),'utf8'));
const proxy=compile(readFileSync(new URL('../src/features/realtime/stream-proxy.ts',import.meta.url),'utf8').replace('from "./frames"',`from "${frames}"`));
const boundary=compile(readFileSync(new URL('../src/features/operator-session/boundary.ts',import.meta.url),'utf8'));
const source=readFileSync(new URL('../src/app/api/realtime/events/route.ts',import.meta.url),'utf8').replace('import {cookies} from "next/headers";','const cookies=()=>globalThis.realtimeCookies();').replace('from "@/features/operator-session/boundary"',`from "${boundary}"`).replace('from "@/features/realtime/stream-proxy"',`from "${proxy}"`);
const route=await import(compile(source));
test('realtime route mediates the private session and only the fixed local stream',async()=>{
 const oldFetch=globalThis.fetch,oldCookies=globalThis.realtimeCookies,calls=[];
 try{
  globalThis.realtimeCookies=()=>({get:()=>undefined});
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response('',{headers:{'Content-Type':'text/event-stream'}});};
  assert.equal((await route.GET(new Request('http://127.0.0.1:3000/api/realtime/events'))).status,401);assert.equal(calls.length,0);
  globalThis.realtimeCookies=()=>({get:()=>({value:'fixture-private-session'})});
  assert.equal((await route.GET(new Request('http://127.0.0.1:3000/api/realtime/events?token=refused'))).status,422);assert.equal(calls.length,0);
  const response=await route.GET(new Request('http://127.0.0.1:3000/api/realtime/events'));
  assert.equal(response.status,200);assert.equal(await response.text(),'');assert.equal(calls.length,1);
  assert.equal(new URL(calls[0].url).pathname,'/api/v1/realtime/events');assert.equal(calls[0].options.headers['X-Operator-Session'],'fixture-private-session');assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.redirect,'error');assert(calls[0].options.signal instanceof AbortSignal);
  globalThis.fetch=async()=>new Response(null,{status:401});assert.equal((await route.GET(new Request('http://127.0.0.1:3000/api/realtime/events'))).status,401);
 }finally{globalThis.fetch=oldFetch;if(oldCookies===undefined)delete globalThis.realtimeCookies;else globalThis.realtimeCookies=oldCookies;}
});

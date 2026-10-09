import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const moduleUrl=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
const boundary=moduleUrl(readFileSync(new URL('../src/features/operator-session/boundary.ts',import.meta.url),'utf8'));
const source=readFileSync(new URL('../src/features/operator-session/server.ts',import.meta.url),'utf8').replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.brokerCookies();').replace('import { NextResponse } from "next/server";','const NextResponse={};').replace('from "./boundary"',`from "${boundary}"`);
const {readBrokerCapabilities}=await import(moduleUrl(source));
test('broker capabilities read keeps credentials server-side and uses only fixed local path',async()=>{
 const previous=globalThis.fetch, cookie=globalThis.brokerCookies, calls=[];
 try {
  globalThis.brokerCookies=()=>({get:()=>undefined});
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify({configured:false}));};
  assert.deepEqual(await readBrokerCapabilities(),{ok:false,reason:'SIGN_IN'});assert.equal(calls.length,0);
  globalThis.brokerCookies=()=>({get:()=>({value:'fixture-private-session'})});
  assert.deepEqual(await readBrokerCapabilities(),{ok:true,value:{configured:false}});
  assert.equal(new URL(calls[0].url).pathname,'/api/v1/brokers/capabilities');
  assert.equal(calls[0].options.headers['X-Operator-Session'],'fixture-private-session');
  assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.redirect,'error');assert(calls[0].options.signal instanceof AbortSignal);
  globalThis.fetch=async()=>new Response(null,{status:401});assert.deepEqual(await readBrokerCapabilities(),{ok:false,reason:'SIGN_IN'});
  globalThis.fetch=async()=>{throw new Error('fixture private diagnostics');};assert.deepEqual(await readBrokerCapabilities(),{ok:false,reason:'UNAVAILABLE'});
 } finally {globalThis.fetch=previous;if(cookie===undefined)delete globalThis.brokerCookies;else globalThis.brokerCookies=cookie;}
});

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const moduleUrl=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
const boundary=moduleUrl(readFileSync(new URL('../src/features/operator-session/boundary.ts',import.meta.url),'utf8'));
const source=readFileSync(new URL('../src/features/operator-session/server.ts',import.meta.url),'utf8').replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.healthCookies();').replace('import { NextResponse } from "next/server";','const NextResponse={};').replace('from "./boundary"',`from "${boundary}"`);
const {readBrokerHealth}=await import(moduleUrl(source));
test('broker health is a fixed authenticated no-store read with clean failure states',async()=>{
 const previous=globalThis.fetch,cookie=globalThis.healthCookies,calls=[];
 try {
  globalThis.healthCookies=()=>({get:()=>undefined});globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response('{}');};
  assert.deepEqual(await readBrokerHealth(),{ok:false,reason:'SIGN_IN'});assert.equal(calls.length,0);
  globalThis.healthCookies=()=>({get:()=>({value:'fixture-private-session'})});
  assert.deepEqual(await readBrokerHealth(),{ok:true,value:{}});assert.equal(new URL(calls[0].url).pathname,'/api/v1/brokers/health');
  assert.equal(calls[0].options.headers['X-Operator-Session'],'fixture-private-session');assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.redirect,'error');
  globalThis.fetch=async()=>new Response(null,{status:401});assert.deepEqual(await readBrokerHealth(),{ok:false,reason:'SIGN_IN'});
  globalThis.fetch=async()=>{throw new Error('fixture private diagnostics');};assert.deepEqual(await readBrokerHealth(),{ok:false,reason:'UNAVAILABLE'});
 } finally {globalThis.fetch=previous;if(cookie===undefined)delete globalThis.healthCookies;else globalThis.healthCookies=cookie;}
});

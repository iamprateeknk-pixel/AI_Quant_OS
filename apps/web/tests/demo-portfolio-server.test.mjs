import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const moduleUrl=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
const boundary=moduleUrl(readFileSync(new URL('../src/features/operator-session/boundary.ts',import.meta.url),'utf8'));
const source=readFileSync(new URL('../src/features/operator-session/server.ts',import.meta.url),'utf8').replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.portfolioCookies();').replace('import { NextResponse } from "next/server";','const NextResponse={};').replace('from "./boundary"',`from "${boundary}"`);
const server=await import(moduleUrl(source));
test('private portfolio read requires owner session, fixed source, no cache and bounded response',async()=>{
 assert.equal(typeof server.readDemoPortfolio,'function');
 const previous=globalThis.fetch,cookie=globalThis.portfolioCookies,calls=[];
 try{
  globalThis.portfolioCookies=()=>({get:()=>undefined});globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response('{"configured":false}');};
  assert.deepEqual(await server.readDemoPortfolio(),{ok:false,reason:'SIGN_IN'});assert.equal(calls.length,0);
  globalThis.portfolioCookies=()=>({get:()=>({value:'fixture-private-session'})});assert.equal((await server.readDemoPortfolio()).ok,true);
  assert.equal(new URL(calls[0].url).pathname,'/api/v1/portfolio/demo-observation');assert.equal(calls[0].options.headers['X-Operator-Session'],'fixture-private-session');assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.redirect,'error');assert(calls[0].options.signal instanceof AbortSignal);
  globalThis.fetch=async()=>new Response(null,{status:401});assert.deepEqual(await server.readDemoPortfolio(),{ok:false,reason:'SIGN_IN'});
  globalThis.fetch=async()=>new Response('x'.repeat(131073));assert.deepEqual(await server.readDemoPortfolio(),{ok:false,reason:'UNAVAILABLE'});
 }finally{globalThis.fetch=previous;if(cookie===undefined)delete globalThis.portfolioCookies;else globalThis.portfolioCookies=cookie;}
});

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
function moduleUrl(source) {return `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;}
const boundary=moduleUrl(readFileSync(new URL('../src/features/operator-session/boundary.ts',import.meta.url),'utf8'));
const source=readFileSync(new URL('../src/features/operator-session/server.ts',import.meta.url),'utf8').replace('import {parsePaperReconciliation} from "../orders/reconciliation";','').replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies = () => globalThis.traceCookies();').replace('import { NextResponse } from "next/server";','const NextResponse = {};').replace('from "./boundary"',`from "${boundary}"`);
const {readGraphRuns,readGraphTrace}=await import(moduleUrl(source));
test('trace reads admit only fixed local paths and server session credentials',async()=>{
  const previous=globalThis.fetch, cookies=globalThis.traceCookies;
  const calls=[];
  try {
    globalThis.traceCookies=()=>({get:()=>undefined});
    globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify({observed:true}));};
    assert.deepEqual(await readGraphRuns(),{ok:false,reason:'SIGN_IN'});
    assert.equal(calls.length,0);
    globalThis.traceCookies=()=>({get:()=>({value:'private-server-session'})});
    assert.deepEqual(await readGraphRuns(),{ok:true,value:{observed:true}});
    assert.deepEqual(await readGraphTrace('run_1'),{ok:true,value:{observed:true}});
    assert.equal(new URL(calls[0].url).pathname,'/api/v1/agents/graph-runs');
    assert.equal(new URL(calls[1].url).pathname,'/api/v1/agents/graph-runs/run_1/trace');
    for (const {options} of calls) {assert.equal(options.headers['X-Operator-Session'],'private-server-session');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');assert(options.signal instanceof AbortSignal);}
    for(const bad of ['../orders','run?token=x','https://example.invalid','a'.repeat(129)]) assert.deepEqual(await readGraphTrace(bad),{ok:false,reason:'INVALID_TRACE'});
    assert.deepEqual(await readGraphRuns('run_1'),{ok:false,reason:'INVALID_PAGE'});
    assert.equal(calls.length,2);
    globalThis.fetch=async()=>new Response(null,{status:404});
    assert.deepEqual(await readGraphTrace('missing'),{ok:false,reason:'NOT_FOUND'});
    globalThis.fetch=async()=>new Response(null,{status:409});
    assert.deepEqual(await readGraphRuns('run_1','a'.repeat(64)),{ok:false,reason:'CHANGED'});
  } finally {globalThis.fetch=previous;if(cookies===undefined)delete globalThis.traceCookies;else globalThis.traceCookies=cookies;}
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
function moduleUrl(source) {
  const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
  return `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`;
}
const boundary=moduleUrl(readFileSync(new URL('../src/features/operator-session/boundary.ts',import.meta.url),'utf8'));
const original=readFileSync(new URL('../src/features/operator-session/server.ts',import.meta.url),'utf8');
const source=original.replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies = () => globalThis.testCookies();').replace('import { NextResponse } from "next/server";','const NextResponse = {};').replace('from "./boundary"',`from "${boundary}"`);
const {readHealth,readDossiers,readGenomes}=await import(moduleUrl(source));
test('protected reads use only server session and fixed private paths',async()=>{
  const previousFetch=globalThis.fetch, previousCookies=globalThis.testCookies, previousOrigin=process.env.AI_QUANT_API_URL;
  let calls=[];
  try {
    process.env.AI_QUANT_API_URL='http://127.0.0.1:8000';
    globalThis.testCookies=()=>({get:()=>undefined});
    globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify({observed:true}));};
    assert.deepEqual(await readGenomes(),{ok:false,reason:'SIGN_IN'});
    assert.equal(calls.length,0);
    globalThis.testCookies=()=>({get:name=>name==='ai_quant_operator'?{value:'synthetic-server-only-nonce'}:undefined});
    for(const read of [()=>readHealth('status'),()=>readDossiers(),()=>readGenomes()]) assert.deepEqual(await read(),{ok:true,value:{observed:true}});
    assert.deepEqual(calls.map(call=>new URL(call.url).pathname),['/api/v1/paper/health','/api/v1/research/candidates/page','/api/v1/evolution/genomes/page']);
    for(const {options} of calls) {assert.equal(options.headers['X-Operator-Session'],'synthetic-server-only-nonce');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');assert(options.signal instanceof AbortSignal);}
    assert.deepEqual(await readGenomes(1),{ok:false,reason:'INVALID_PAGE'});
    assert.equal(calls.length,3);
    for(const [status,reason] of [[401,'SIGN_IN'],[409,'CHANGED'],[503,'UNAVAILABLE']]) {
      globalThis.fetch=async()=>new Response(null,{status});
      assert.deepEqual(await readGenomes(),{ok:false,reason});
    }
    globalThis.fetch=async()=>new Response(JSON.stringify({text:'x'.repeat(131072)}));
    assert.deepEqual(await readGenomes(),{ok:false,reason:'UNAVAILABLE'});
    calls=[];process.env.AI_QUANT_API_URL='https://example.invalid';
    globalThis.fetch=async()=>{calls.push(1);throw new Error('Must not fetch');};
    assert.deepEqual(await readGenomes(),{ok:false,reason:'UNAVAILABLE'});
    assert.equal(calls.length,0);
  } finally {
    globalThis.fetch=previousFetch;
    if(previousCookies===undefined) delete globalThis.testCookies;else globalThis.testCookies=previousCookies;
    if(previousOrigin===undefined) delete process.env.AI_QUANT_API_URL;else process.env.AI_QUANT_API_URL=previousOrigin;
  }
});

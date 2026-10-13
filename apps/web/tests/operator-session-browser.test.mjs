import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const source=readFileSync(new URL('../src/features/operator-session/browser.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {hasOperatorSession}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('operator session probe accepts only the exact authorized response and uses a private uncached request',async()=>{
 const previous=globalThis.fetch,calls=[];
 try{
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify({authorized:true}));};
  assert.equal(await hasOperatorSession(),true);
  assert.equal(calls[0].url,'/api/operator/session/authorization');
  assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.credentials,'same-origin');
  globalThis.fetch=async()=>new Response(JSON.stringify({authorized:false}));assert.equal(await hasOperatorSession(),false);
  globalThis.fetch=async()=>new Response(JSON.stringify({authorized:true,token:'private'}));assert.equal(await hasOperatorSession(),false);
  globalThis.fetch=async()=>new Response(null,{status:401});assert.equal(await hasOperatorSession(),false);
  globalThis.fetch=async()=>{throw new Error('unavailable');};assert.equal(await hasOperatorSession(),false);
 }finally{globalThis.fetch=previous;}
});

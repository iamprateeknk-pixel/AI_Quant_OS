import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const moduleUrl=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
const boundary=moduleUrl(readFileSync(new URL('../src/features/operator-session/boundary.ts',import.meta.url),'utf8'));
const reconciliation=moduleUrl(readFileSync(new URL('../src/features/orders/reconciliation.ts',import.meta.url),'utf8'));
const source=readFileSync(new URL('../src/features/operator-session/server.ts',import.meta.url),'utf8').replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.reconciliationCookies();').replace('import { NextResponse } from "next/server";','const NextResponse={json:(body,options)=>Response.json(body,options)};').replace('from "./boundary"',`from "${boundary}"`).replace('from "../orders/reconciliation"',`from "${reconciliation}"`);
const {submitPaperPositionReconciliation}=await import(moduleUrl(source));
const response={schema_version:'1.1',summary_instrument_id:'EURUSD',matches:true,local_net_quantity:'0',broker_net_quantity:'0',local_cash_flow_quote:'0',broker_cash_flow_quote:'0',local_only_fill_ids:[],broker_only_fill_ids:[],conflicting_fill_ids:[],instruments:[]};
test('paper reconciliation enforces same origin, explicit intent, fixed endpoint and operator session',async()=>{
 const previous=globalThis.fetch,cookie=globalThis.reconciliationCookies,calls=[];
 try{
  globalThis.reconciliationCookies=()=>({get:()=>undefined});
  const request=()=>new Request('http://127.0.0.1:3000/api/orders/reconcile',{method:'POST',headers:{origin:'http://127.0.0.1:3000',host:'127.0.0.1:3000','content-type':'application/json'},body:JSON.stringify({confirmation:'COMPARE'})});
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return Response.json(response);};
  let result=await submitPaperPositionReconciliation(new Request('http://127.0.0.1:3000/api/orders/reconcile',{method:'POST',headers:{origin:'https://attacker.invalid',host:'127.0.0.1:3000','content-type':'application/json'},body:'{"confirmation":"COMPARE"}'}));
  assert.equal(result.status,403);assert.equal(calls.length,0);
  result=await submitPaperPositionReconciliation(request());assert.equal(result.status,401);assert.equal(calls.length,0);
  globalThis.reconciliationCookies=()=>({get:()=>({value:'fixture-private-session'})});
  result=await submitPaperPositionReconciliation(new Request('http://127.0.0.1:3000/api/orders/reconcile',{method:'POST',headers:{origin:'http://127.0.0.1:3000',host:'127.0.0.1:3000','content-type':'application/json'},body:'{}'}));assert.equal(result.status,422);assert.equal(calls.length,0);
  result=await submitPaperPositionReconciliation(request());assert.equal(result.status,200);const projected=await result.json();assert.equal(projected.matches,true);assert.equal(projected.local_net_quantity,'0');assert.deepEqual(projected.instruments,[]);
  assert.equal(new URL(calls[0].url).pathname,'/api/v1/paper/positions/reconcile');assert.equal(calls[0].options.method,'POST');
  assert.equal(calls[0].options.headers['X-Operator-Session'],'fixture-private-session');assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.redirect,'error');
  globalThis.fetch=async()=>new Response(null,{status:503});result=await submitPaperPositionReconciliation(request());assert.equal(result.status,503);assert(!JSON.stringify(await result.json()).includes('private'));
 }finally{globalThis.fetch=previous;if(cookie===undefined)delete globalThis.reconciliationCookies;else globalThis.reconciliationCookies=cookie;}
});

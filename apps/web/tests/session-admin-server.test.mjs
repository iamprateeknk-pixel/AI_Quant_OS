import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const moduleUrl=s=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(s,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
const boundary=moduleUrl(readFileSync(new URL('../src/features/operator-session/boundary.ts',import.meta.url),'utf8'));
const source=readFileSync(new URL('../src/features/operator-session/server.ts',import.meta.url),'utf8').replace('import {parsePaperReconciliation} from "../orders/reconciliation";','').replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.adminCookies();').replace('import { NextResponse } from "next/server";','const NextResponse=globalThis.adminResponse;').replace('from "./boundary"',`from "${boundary}"`);
globalThis.adminResponse={json:(v,o)=>{const r=new Response(JSON.stringify(v),o);r.cookies={set:(...args)=>{r.cookie=args}};return r}};
const server=await import(moduleUrl(source));
delete globalThis.adminResponse;
const actor='12345678-1234-4234-8234-123456789abc',target='22345678-1234-4234-8234-123456789abc';
const session={mode:'PAPER',session_id:actor,created_at:'2026-10-09T12:00:00+00:00',expires_at:'2026-10-09T12:15:00+00:00'};
const inventory={schema_version:'1.0',mode:'PAPER',scope:'LOCAL_SINGLE_OWNER',observed_at:session.created_at,current_session_id:actor,source_head:'a'.repeat(64),source_event_count:1,sessions:[{...session,status:'CURRENT'}]};
test('session administrative proxy denies cross-origin/invalid targets and keeps nonce server-side',async()=>{
 assert.equal(typeof server.readSessions,'function'); assert.equal(typeof server.revokeSession,'function');
 const oldFetch=globalThis.fetch,oldCookies=globalThis.adminCookies;
 const origins=[process.env.AI_QUANT_API_URL,process.env.AI_QUANT_WEB_ORIGIN];
 let calls=[];
 try{
 process.env.AI_QUANT_API_URL='http://127.0.0.1:8000';process.env.AI_QUANT_WEB_ORIGIN='http://127.0.0.1:3000';
 globalThis.adminCookies=()=>({get:()=>({value:'synthetic-server-nonce'})});
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(url.endsWith('/session')?session:url.endsWith('/revoke')?{schema_version:'1.0',mode:'PAPER',status:'REVOKED',target_session_id:target,actor_session_id:actor,current_session_revoked:false,revoked_at:session.created_at,source_head:'b'.repeat(64),credential:'private'}:{...inventory,session_token:'private'}))};
 assert.deepEqual(await server.readSessions(),{ok:true,value:inventory});
 const request=(origin,body)=>new Request('http://127.0.0.1:3000/api/operator/sessions/revoke',{method:'POST',headers:{Host:'127.0.0.1:3000',Origin:origin,'Sec-Fetch-Site':'same-origin','Content-Type':'application/json'},body:JSON.stringify(body)});
 const body={credential:'fixture-access',target_session_id:target,expected_head:'a'.repeat(64)};
 calls=[];assert.equal((await server.revokeSession(request('https://example.invalid',body))).status,403);assert.equal(calls.length,0);
 assert.equal((await server.revokeSession(request('http://127.0.0.1:3000',{...body,role:'ADMIN'}))).status,422);assert.equal(calls.length,0);
 const result=await server.revokeSession(request('http://127.0.0.1:3000',body));assert.equal(result.status,200);
 assert.deepEqual(calls.map(c=>new URL(c.url).pathname),['/api/v1/operator/session','/api/v1/operator/sessions/revoke']);
 const command=calls[1].options;assert.equal(command.headers['X-Operator-Reauth'],'fixture-access');assert.equal(command.headers['X-Operator-Session'],'synthetic-server-nonce');assert.deepEqual(JSON.parse(command.body),{target_session_id:target,expected_head:'a'.repeat(64)});
 assert(!JSON.stringify(await result.json()).includes('private'));
 globalThis.fetch=async()=>new Response(null,{status:409});assert.equal((await server.revokeSession(request('http://127.0.0.1:3000',body))).status,409);
 }finally{globalThis.fetch=oldFetch;if(oldCookies===undefined)delete globalThis.adminCookies;else globalThis.adminCookies=oldCookies;for(const [i,key] of ['AI_QUANT_API_URL','AI_QUANT_WEB_ORIGIN'].entries())if(origins[i]===undefined)delete process.env[key];else process.env[key]=origins[i]}
});


test('self revocation clears the HttpOnly session only after a verified receipt',async()=>{
 const oldFetch=globalThis.fetch,oldCookies=globalThis.adminCookies;
 const origins=[process.env.AI_QUANT_API_URL,process.env.AI_QUANT_WEB_ORIGIN];
 try{
 process.env.AI_QUANT_API_URL='http://127.0.0.1:8000';process.env.AI_QUANT_WEB_ORIGIN='http://127.0.0.1:3000';
 globalThis.adminCookies=()=>({get:()=>({value:'synthetic-server-nonce'})});
 globalThis.fetch=async(url)=>new Response(JSON.stringify(url.endsWith('/session')?session:{schema_version:'1.0',mode:'PAPER',status:'REVOKED',target_session_id:actor,actor_session_id:actor,current_session_revoked:true,revoked_at:session.created_at,source_head:'b'.repeat(64)}));
 const request=()=>new Request('http://127.0.0.1:3000/api/operator/sessions/revoke',{method:'POST',headers:{Host:'127.0.0.1:3000',Origin:'http://127.0.0.1:3000'},body:JSON.stringify({credential:'fixture-access',target_session_id:actor,expected_head:'a'.repeat(64)})});
 const result=await server.revokeSession(request());assert.equal(result.status,200);assert.equal(result.cookie[0],'ai_quant_operator');assert.equal(result.cookie[1],'');assert.equal(result.cookie[2].maxAge,0);assert.equal(result.cookie[2].httpOnly,true);
 globalThis.fetch=async(url)=>new Response(JSON.stringify(url.endsWith('/session')?session:{status:'PENDING'}));
 const unconfirmed=await server.revokeSession(request());assert.equal(unconfirmed.status,503);assert.equal(unconfirmed.cookie,undefined);
 globalThis.adminCookies=()=>({get:()=>undefined});let calls=0;globalThis.fetch=async()=>{calls++;throw new Error('Must not dispatch')};assert.equal((await server.revokeSession(request())).status,401);assert.equal(calls,0);
 }finally{globalThis.fetch=oldFetch;if(oldCookies===undefined)delete globalThis.adminCookies;else globalThis.adminCookies=oldCookies;for(const [i,key] of ['AI_QUANT_API_URL','AI_QUANT_WEB_ORIGIN'].entries())if(origins[i]===undefined)delete process.env[key];else process.env[key]=origins[i]}
});

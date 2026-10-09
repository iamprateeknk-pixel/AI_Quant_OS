import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
function moduleUrl(source){return `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;}
const {publicSessionInventory,publicSessionRevocation}=await import(moduleUrl(readFileSync(new URL('../src/features/operator-session/boundary.ts',import.meta.url),'utf8')));
export const actor='12345678-1234-4234-8234-123456789abc',target='22345678-1234-4234-8234-123456789abc';
export const view={schema_version:'1.0',mode:'PAPER',scope:'LOCAL_SINGLE_OWNER',observed_at:'2026-10-09T12:00:00+00:00',current_session_id:actor,source_head:'a'.repeat(64),source_event_count:2,sessions:[{mode:'PAPER',session_id:actor,created_at:'2026-10-09T12:00:00+00:00',expires_at:'2026-10-09T12:15:00+00:00',status:'CURRENT'},{mode:'PAPER',session_id:target,created_at:'2026-10-09T12:00:00+00:00',expires_at:'2026-10-09T12:15:00+00:00',status:'ACTIVE'}]};
test('inventory projects only public metadata and checks exact current identity',()=>{
 assert.equal(typeof publicSessionInventory,'function');
 assert.deepEqual(publicSessionInventory({...view,session_token:'private',sessions:view.sessions.map(row=>({...row,token_sha256:'private'}))}),view);
 for(const raw of [{...view,mode:'LIVE'},{...view,scope:'ALL_USERS'},{...view,current_session_id:target},{...view,sessions:[view.sessions[0],view.sessions[0]]},{...view,sessions:[]},{...view,source_event_count:0},{...view,source_head:'bad'},{...view,sessions:[view.sessions[0],{...view.sessions[1],status:'EXPIRED'}]}]) assert.throws(()=>publicSessionInventory(raw));
});
test('revocation projection binds target, actor and signed-out state',()=>{
 assert.equal(typeof publicSessionRevocation,'function');
 const receipt={schema_version:'1.0',mode:'PAPER',status:'REVOKED',target_session_id:target,actor_session_id:actor,current_session_revoked:false,revoked_at:view.observed_at,source_head:'b'.repeat(64)};
 assert.deepEqual(publicSessionRevocation({...receipt,credential:'private'},target,actor),receipt);
 for(const raw of [{...receipt,status:'PENDING'},{...receipt,target_session_id:actor},{...receipt,current_session_revoked:true},{...receipt,actor_session_id:target}]) assert.throws(()=>publicSessionRevocation(raw,target,actor));
});

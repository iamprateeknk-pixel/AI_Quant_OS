import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../src/features/brokers/demo-sync-health.ts',import.meta.url),'utf8');
const {parseDemoSyncHealth}=await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);
import {now,health,record,payload} from './fixtures/demo-sync.mjs';
test('demo health projects public metadata and labels age at render time',()=>{
  const view=parseDemoSyncHealth({...payload,password:'private',observation:{...payload.observation,record:{...record,terminal_path:'private'}}},now);
  assert.equal(view.record.health.state,'HEALTHY');assert.equal(view.state,'AVAILABLE');
  assert(!JSON.stringify(view).includes('private'));
  assert.equal(parseDemoSyncHealth(payload,'2026-10-09T12:00:30Z').state,'STALE');
  assert.equal(parseDemoSyncHealth(payload,'2026-10-09T11:59:59Z'),null);
});
test('missing and unconfigured observations do not fabricate healthy service',()=>{
  assert.deepEqual(parseDemoSyncHealth({...payload,configured:false,observation:{...payload.observation,state:'UNAVAILABLE',record:null}},now),{configured:false,state:'UNAVAILABLE',record:null});
  assert.equal(parseDemoSyncHealth({...payload,configured:false},now),null);
  assert.equal(parseDemoSyncHealth({...payload,observation:{...payload.observation,record:null}},now),null);
});
test('authority, native attestation, timestamps and pending intent remain bounded',()=>{
  for(const bad of [
    {...payload,may_submit_orders:true},
    {...payload,observation:{...payload.observation,record:{...record,native_attestation:'VERIFIED'}}},
    {...payload,observation:{...payload.observation,record:{...record,valid_until:'2026-10-09T13:00:00Z'}}},
    {...payload,observation:{...payload.observation,record:{...record,health:{...health,state:'ACTIVE'}}}},
    {...payload,observation:{...payload.observation,record:{...record,health:{...health,consecutive_failures:3001}}}},
    {...payload,observation:{...payload.observation,record:{...record,health:{...health,code:'private error'}}}},
  ]) assert.equal(parseDemoSyncHealth(bad,now),null);
});

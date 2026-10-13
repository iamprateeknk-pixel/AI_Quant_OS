import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const path = new URL('../src/features/research/dossiers.ts', import.meta.url);
const source = readFileSync(path,'utf8');
const compiled = ts.transpileModule(source,{ compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const { parseDossiers } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const row={candidate_id:'a'.repeat(64),experiment_id:'experiment',hypothesis_id:'hypothesis',hypothesis:'Paper hypothesis',state:'CHALLENGER',outcome:'VALIDATION_INCOMPLETE',revision:1,report_sha256:'b'.repeat(64),plan_sha256:'c'.repeat(64),last_event_sha256:'d'.repeat(64),strategy_ref:'momentum@1.0.0',fold_count:2,attempt_count:8,gates:[{name:'SANDBOX',state:'PENDING',reason:'Missing evidence'}],reasons:[],details_truncated:false,promotion_allowed:false};
const page={schema_version:'1.0',mode:'PAPER',head_hash:'e'.repeat(64),total:1,offset:0,limit:2,next_offset:null,candidates:[row]};
test('retains archived gate states and explicit truncated preview status',()=>{
  assert.deepEqual(parseDossiers(page).candidates,[row]);
  assert.equal(parseDossiers({...page,candidates:[{...row,details_truncated:true}]}).candidates[0].details_truncated,true);
});
test('renders a threshold-demoted candidate as retained diagnostic metadata',()=>{
  const demoted={...row,state:'DEMOTED',revision:2};
  assert.deepEqual(parseDossiers({...page,candidates:[demoted]}).candidates,[demoted]);
});
test('withholds malformed and capital-authorizing dossier responses',()=>{
  for(const payload of [{...page,mode:'LIVE'},{...page,total:0},{...page,next_offset:2},{...page,candidates:[{...row,promotion_allowed:true}]},{...page,candidates:[{...row,state:'LIVE'}]},{...page,candidates:[{...row,gates:[{name:'SANDBOX',state:'READY',reason:'x'}]}]}]) assert.equal(parseDossiers(payload),null);
});
test('projects only metadata and distinguishes an empty archive',()=>{
  assert.deepEqual(parseDossiers({...page,total:0,candidates:[]}).candidates,[]);
  assert.deepEqual(parseDossiers({...page,candidates:[{...row,sources:['untrusted source text']}]}).candidates,[row]);
});

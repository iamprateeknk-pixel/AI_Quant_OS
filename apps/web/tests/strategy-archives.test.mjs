import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const path=new URL('../src/features/strategies/archives.ts',import.meta.url);
const source=readFileSync(path,'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {parseStrategies}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const row={strategy_ref:'moving-average-cross@1.0.0',input_kind:'BAR',implementation_sha256:'a'.repeat(64),state:'ARCHIVED_IMPLEMENTATION',parameters:[{name:'fast',value:2}],source_inventory:[{path:'strategy_samples.py',sha256:'b'.repeat(64)}],details_truncated:false,promotion_allowed:false};
const page={schema_version:'1.0',mode:'PAPER',head_hash:'c'.repeat(64),total:1,offset:0,limit:10,next_offset:null,strategies:[row]};
test('projects saved implementation metadata without source content',()=>{
  assert.deepEqual(parseStrategies({...page,strategies:[{...row,content:'private source'}]}).strategies,[row]);
  assert.deepEqual(parseStrategies({...page,total:0,strategies:[]}).strategies,[]);
});
test('rejects approval claims, duplicate versions and invalid page data',()=>{
  for(const bad of [{...page,mode:'LIVE'},{...page,total:0},{...page,next_offset:10},{...page,total:2,strategies:[row,row]},{...page,strategies:[{...row,state:'APPROVED'}]},{...page,strategies:[{...row,promotion_allowed:true}]},{...page,strategies:[{...row,parameters:[{name:'fast',value:true}]}]}]) assert.equal(parseStrategies(bad),null);
});

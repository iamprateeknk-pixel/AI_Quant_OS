import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const path = new URL('../src/features/evolution/genomes.ts', import.meta.url);
const source = readFileSync(path,'utf8');
const compiled = ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const { parseGenomes } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const row={genome_ref:'agent-genome:'+'a'.repeat(64),agent_id:'A02',agent_version:'0.2.0',manifest_ref:'agent-manifest:'+'b'.repeat(64),parent_ref:null,state:'ARCHIVED_BASELINE',objective:{schema_version:'1.0',metric:'CORRECTNESS',direction:'MAXIMIZE',minimum_improvement:'0.01',protocol_sha256:'c'.repeat(64)},source_inventory:[{path:'src/ai_quant_os/regime_agent.py',sha256:'d'.repeat(64)}],execution_verified:false,promotion_allowed:false,details_truncated:false};
const page={schema_version:'1.0',mode:'PAPER',head_hash:'e'.repeat(64),total:1,offset:0,limit:10,next_offset:null,genomes:[row]};
test('projects archival lineage without source or prompts',()=>{
  assert.deepEqual(parseGenomes({...page,genomes:[{...row,sources:['private'],prompt_template:'private'}]}).genomes,[row]);
  assert.deepEqual(parseGenomes({...page,total:0,genomes:[]}).genomes,[]);
});
test('rejects inconsistent lineage, evaluation directions and authority claims',()=>{
  for(const bad of [{...row,parent_ref:row.genome_ref},{...row,state:'CHAMPION'},{...row,execution_verified:true},{...row,promotion_allowed:true},{...row,objective:{...row.objective,direction:'MINIMIZE'}},{...row,source_inventory:[...row.source_inventory,...row.source_inventory]}]) assert.equal(parseGenomes({...page,genomes:[bad]}),null);
  assert.equal(parseGenomes({...page,total:0}),null);
  assert.equal(parseGenomes({...page,next_offset:10}),null);
});
test('omitted exact objectives require an explicit preview flag',()=>{
  const omitted={...row,details_truncated:true,objective:{...row.objective,minimum_improvement:null}};
  assert.deepEqual(parseGenomes({...page,genomes:[omitted]}).genomes,[omitted]);
  assert.equal(parseGenomes({...page,genomes:[{...omitted,details_truncated:false}]}),null);
});

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../src/features/orders/reconciliation.ts',import.meta.url),'utf8');
const {parsePaperReconciliation}=await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);
const row={instrument_id:'EURUSD',matches:true,local_net_quantity:'0',broker_net_quantity:'0',local_cash_flow_quote:'0',broker_cash_flow_quote:'0'};
const receipt={schema_version:'1.1',summary_instrument_id:'EURUSD',matches:true,local_net_quantity:'0',broker_net_quantity:'0',local_cash_flow_quote:'0',broker_cash_flow_quote:'0',local_only_fill_ids:[],broker_only_fill_ids:[],conflicting_fill_ids:[],instruments:[row]};
test('paper reconciliation projects a matching instrument ledger',()=>{
 const result=parsePaperReconciliation(receipt);assert.equal(result.matches,true);assert.deepEqual(result.instruments,[row]);
});
test('paper reconciliation preserves a mismatch and its diff references',()=>{
 const result=parsePaperReconciliation({...receipt,matches:false,local_net_quantity:'1',local_only_fill_ids:['fill-1'],instruments:[{...row,matches:false,local_net_quantity:'1'}]});
 assert.equal(result.matches,false);assert.equal(result.instruments[0].local_net_quantity,'1');assert.deepEqual(result.local_only_fill_ids,['fill-1']);
});
test('paper reconciliation rejects malformed identity, unbounded fields, and inconsistent summary',()=>{
 for(const bad of [
  {...receipt,schema_version:'1.0'},
  {...receipt,summary_instrument_id:'GBPUSD'},
  {...receipt,matches:false},
  {...receipt,instruments:[{...row,instrument_id:'UNKNOWN'}]},
  {...receipt,local_only_fill_ids:Array(257).fill('fill')},
 ]) assert.equal(parsePaperReconciliation(bad),null);
});

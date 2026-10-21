import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../src/features/brokers/capabilities.ts',import.meta.url),'utf8');
const {parseBrokerCapabilities}=await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);
const fixture=JSON.parse(readFileSync(new URL('./fixtures/broker-capabilities.json',import.meta.url),'utf8'));
test('broker registry projects only current public metadata from backend fixture',()=>{
 const view=parseBrokerCapabilities({...fixture,password:'fixture-private',brokers:fixture.brokers.map(row=>({...row,account_login:'fixture-private'}))});
 assert.equal(view.length,5);assert.equal(view[0].paper_admission_policy_allows,true);
 assert.equal(view[1].implementation,'ISOLATED_DEMO_ORDER_CLI');
 assert(view[1].operations.includes('DEMO_ORDER_SUBMIT_EXPLICIT'));
 assert(view[1].operations.includes('DEMO_ORDER_RECONCILE_READ_ONLY'));
 assert(!JSON.stringify(view).includes('fixture-private'));assert(!JSON.stringify(view).includes('source_module'));
});
test('missing duplicate unknown and unbounded profiles are withheld',()=>{
 for(const rows of [[],fixture.brokers.slice(1),[...fixture.brokers,fixture.brokers[0]],fixture.brokers.map((row,i)=>i===1?fixture.brokers[0]:row),fixture.brokers.map((row,i)=>i===4?{...row,broker_id:'UNKNOWN'}:row)]) assert.equal(parseBrokerCapabilities({...fixture,brokers:rows}),null);
});
test('unsupported capability or native release claims cannot be displayed as ready',()=>{
 for(const changed of [
  {...fixture,live_execution_released:true},
  {...fixture,brokers:fixture.brokers.map(row=>row.broker_id==='IBKR'?{...row,operations:['SIMULATED_ORDER_PLACE']}:row)},
  {...fixture,brokers:fixture.brokers.map(row=>row.broker_id==='IBKR'?{...row,paper_admission_policy_allows:true}:row)},
  {...fixture,brokers:fixture.brokers.map(row=>row.broker_id==='EXNESS_MT5_DEMO'?{...row,native_acceptance:'VERIFIED'}:row)},
  {...fixture,brokers:fixture.brokers.map(row=>row.broker_id==='PAPER'?{...row,eligible:false}:row)},
  {...fixture,brokers:fixture.brokers.map(row=>row.broker_id==='PAPER'?{...row,instruments:['EURUSD','EURUSD']}:row)},
 ]) assert.equal(parseBrokerCapabilities(changed),null);
});

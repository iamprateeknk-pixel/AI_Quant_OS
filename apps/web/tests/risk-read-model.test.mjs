import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const source=readFileSync(new URL('../src/features/risk/read-model.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {parseSystemStatus,parseRiskView}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const system={mode:'PAPER',safety_state:'NO_NEW_RISK',order_count:3};
const risk={instrument_id:'EURUSD',safety_state:'NORMAL',max_order_exposure:'100',max_portfolio_exposure:'1000',worst_case_quantity:'5',filled_net_quantity:'-2.5',pending_buy_quantity:'1',pending_sell_quantity:'0',portfolio_snapshot_id:'paper:empty',reporting_currency:'USD',max_currency_exposure_reporting:'2000',max_gross_currency_exposure_reporting:'4000'};

test('safety and risk readers project only valid PAPER state and bounded exact strings',()=>{
 assert.deepEqual(parseSystemStatus(system),system);
 assert.deepEqual(parseRiskView(risk,'EURUSD'),risk);
 assert.equal(parseRiskView({...risk,filled_net_quantity:'-0.0001'},'EURUSD').filled_net_quantity,'-0.0001');
});

test('safety and risk readers reject unknown fields, invalid states, negative bounds and wrong instruments',()=>{
 for(const bad of [
  {...system,safety_state:'LIVE'},
  {...system,order_count:-1},
  {...system,extra:true},
 ]) assert.equal(parseSystemStatus(bad),null);
 for(const bad of [
  {...risk,safety_state:'LIVE'},
  {...risk,pending_buy_quantity:'-1'},
  {...risk,max_order_exposure:'0'},
  {...risk,worst_case_quantity:'NaN'},
  {...risk,extra:true},
 ]) assert.equal(parseRiskView(bad,'EURUSD'),null);
 assert.equal(parseRiskView({...risk,instrument_id:'GBPUSD'},'EURUSD'),null);
});

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../src/features/brokers/health.ts',import.meta.url),'utf8');
const {parseBrokerHealth}=await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);
const ids=['PAPER','EXNESS_MT5_DEMO','EXNESS','IBKR','ZERODHA'];
const fixture={schema_version:'1.0',mode:'PAPER',live_execution_released:false,brokers:ids.map(broker_id=>({schema_version:'1.0',mode:'PAPER',broker_id,connection_state:'UNKNOWN',quote_age_ms:null,order_latency_ms:null,reject_rate:null,reconciliation_state:'UNASSESSED',spread_state:'UNASSESSED',account_state:'UNASSESSED',health_score:null,updated_at:'2026-10-09T12:00:00Z',live_execution_released:false}))};
test('broker health accepts only the fixed paper catalog and preserves unknown evidence',()=>{
 const rows=parseBrokerHealth(fixture);
 assert.equal(rows.length,5);assert.equal(rows[0].connection_state,'UNKNOWN');assert.equal(rows[0].health_score,null);
 assert.equal(rows[0].reconciliation_state,'UNASSESSED');assert.equal(rows[0].quote_age_ms,null);
});
test('broker health rejects incomplete, duplicate, unknown, scored-without-evidence, or live claims',()=>{
 for(const value of [
  {...fixture,brokers:fixture.brokers.slice(1)},
  {...fixture,brokers:fixture.brokers.map((row,i)=>i===4?{...row,broker_id:'PAPER'}:row)},
  {...fixture,brokers:fixture.brokers.map((row,i)=>i===0?{...row,health_score:0.99}:row)},
  {...fixture,live_execution_released:true},
  {...fixture,brokers:fixture.brokers.map((row,i)=>i===0?{...row,connection_state:'ACTIVE'}:row)},
 ]) assert.equal(parseBrokerHealth(value),null);
});

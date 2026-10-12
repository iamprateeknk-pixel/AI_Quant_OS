import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const source=readFileSync(new URL('../src/features/world-state/snapshot.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {parseWorldState}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const row=(instrument_id)=>({instrument_id,base_currency:instrument_id.slice(0,3),quote_currency:instrument_id.slice(3),quote_status:'FRESH',mid_price:'1.08002',spread_bps:'0.18',history_status:'FRESH',bar_count:48,return_bps:'-2.4',realized_volatility_bps:'5.1',regime:'RANGING',evidence_refs:[`paper-bars:${'b'.repeat(64)}`]});
const twin=(currency,instrument_ids)=>({currency,instrument_ids,fresh_quote_count:instrument_ids.length,macro_available:false});
const world=()=>({schema_version:'1.0',mode:'PAPER',world_state_version:`world:${'a'.repeat(64)}`,as_of:'2026-10-13T10:00:00Z',valid_until:'2026-10-13T10:00:05Z',status:'READY',instruments:[row('EURUSD'),row('GBPUSD'),row('USDJPY'),row('EURJPY')],currency_twins:[twin('EUR',['EURUSD','EURJPY']),twin('GBP',['GBPUSD']),twin('JPY',['EURJPY','USDJPY']),twin('USD',['EURUSD','GBPUSD','USDJPY'])],policy_version:'paper-regime-v1',source:'PAPER_SIMULATED'});

test('world-state parser projects the exact current paper snapshot',()=>{
 const parsed=parseWorldState(world(),Date.parse('2026-10-13T10:00:01Z'));
 assert.equal(parsed.expired,false);assert.equal(parsed.world_state_version,world().world_state_version);
 assert.equal(parsed.instruments[0].return_bps,'-2.4');assert.deepEqual(parsed.currency_twins[3].instrument_ids,['EURUSD','GBPUSD','USDJPY']);
 assert(!Object.hasOwn(parsed,'policy_version'));
});

test('world-state parser rejects extra fields, naive time, incoherent IDs and malformed metrics',()=>{
 const invalid=[
  {...world(),private_payload:'secret'},
  {...world(),as_of:'2026-10-13T10:00:00'},
  {...world(),valid_until:'2026-10-13T10:00:00Z'},
  {...world(),instruments:[row('EURUSD'),row('EURUSD'),row('USDJPY'),row('EURJPY')]},
  {...world(),instruments:[{...row('EURUSD'),spread_bps:'NaN'},row('GBPUSD'),row('USDJPY'),row('EURJPY')]},
  {...world(),instruments:[{...row('EURUSD'),regime:'MAYBE'},row('GBPUSD'),row('USDJPY'),row('EURJPY')]},
  {...world(),currency_twins:[twin('EUR',['MISSING'])]},
 ];
 for(const value of invalid) assert.equal(parseWorldState(value,Date.parse('2026-10-13T10:00:01Z')),null);
});

test('expired snapshots stay typed as expired instead of being refreshed by the parser',()=>{
 const parsed=parseWorldState(world(),Date.parse('2026-10-13T10:00:06Z'));
 assert.equal(parsed.expired,true);
});

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const source=readFileSync(new URL('../src/features/markets/context.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {selectMarketContext}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const quote={instrument_id:'EURUSD',bid:'1.08001',ask:'1.08003',observed_at:'2026-10-13T10:00:00Z',world_state_version:'world:42',freshness:'FRESH',source:'PAPER_SIMULATED'};
const world={expired:false,world_state_version:'world:42',as_of:'2026-10-13T10:00:00Z',valid_until:'2026-10-13T10:00:30Z',status:'READY',instruments:[{instrument_id:'EURUSD',quote_status:'FRESH',history_status:'FRESH',bar_count:48,spread_bps:'0.18',return_bps:'2.4',realized_volatility_bps:'5.1',regime:'RANGING'}],currency_twins:[]};
const ok=value=>({ok:true,value});

test('market context exposes only same-version instrument statistics',()=>{
 const context=selectMarketContext(ok(quote),ok(world),'EURUSD');
 assert.deepEqual(context,{state:'MATCHED',instrument_id:'EURUSD',bar_count:48,spread_bps:'0.18',return_bps:'2.4',realized_volatility_bps:'5.1',regime:'RANGING'});
});

test('market context withholds stale, mismatched, missing, or malformed measurements',()=>{
 assert.equal(selectMarketContext(ok({...quote,freshness:'STALE'}),ok(world),'EURUSD').state,'UNAVAILABLE');
 assert.equal(selectMarketContext(ok(quote),ok({...world,world_state_version:'world:41'}),'EURUSD').state,'UNAVAILABLE');
 assert.equal(selectMarketContext(ok(quote),ok({...world,instruments:[] }),'EURUSD').state,'UNAVAILABLE');
 assert.equal(selectMarketContext(ok(quote),ok({...world,instruments:[{...world.instruments[0],realized_volatility_bps:'NaN'}]}),'EURUSD').state,'UNAVAILABLE');
 assert.equal(selectMarketContext({ok:false,reason:'UNAVAILABLE'},ok(world),'EURUSD').state,'UNAVAILABLE');
});

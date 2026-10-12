import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../src/features/markets/snapshot.ts',import.meta.url),'utf8');
const {parseMarketSnapshot}=await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);
const quote={instrument_id:'EURUSD',bid:'1.08001',ask:'1.08003',observed_at:'2026-10-12T12:00:00Z',world_state_version:`world:${'a'.repeat(64)}`,freshness:'FRESH',source:'PAPER_SIMULATED'};
test('market snapshot projects current paper quote metadata without private fields',()=>{
 const result=parseMarketSnapshot({...quote,account_login:'fixture-private'},'EURUSD');
 assert.equal(result.freshness,'FRESH');assert.equal(result.source,'PAPER_SIMULATED');
 assert(!JSON.stringify(result).includes('fixture-private'));
});
test('market snapshot rejects mismatched, malformed, nonpositive and non-timezone-aware data',()=>{
 for(const bad of [
  {...quote,instrument_id:'GBPUSD'},
  {...quote,bid:'Infinity'},
  {...quote,ask:'0'},
  {...quote,observed_at:'2026-10-12T12:00:00'},
  {...quote,freshness:'UNKNOWN'},
  {...quote,source:'LIVE_BROKER'},
 ]) assert.equal(parseMarketSnapshot(bad,'EURUSD'),null);
});
test('stale paper quote remains visible as stale and source version is bounded',()=>{
 assert.equal(parseMarketSnapshot({...quote,freshness:'STALE'},'EURUSD').freshness,'STALE');
 assert.equal(parseMarketSnapshot({...quote,world_state_version:'x'.repeat(257)},'EURUSD'),null);
});

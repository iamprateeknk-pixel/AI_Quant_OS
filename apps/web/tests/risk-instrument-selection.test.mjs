import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const encode=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`;
let source=readFileSync(new URL('../src/lib/control-api.ts',import.meta.url),'utf8')
 .replace('import "server-only";','')
 .replace('import { parseMarketSnapshot, type MarketSnapshot } from "@/features/markets/snapshot";','')
 .replace('import { parseWorldState, type WorldStateView } from "@/features/world-state/snapshot";','')
 .replace('import { parseRiskView, parseSystemStatus, type RiskView, type SystemStatus } from "@/features/risk/read-model";','');
globalThis.parseRiskView=(value,instrument)=>value.instrument_id===instrument?value:null;
globalThis.parseSystemStatus=value=>value;
globalThis.parseMarketSnapshot=value=>value;
globalThis.parseWorldState=value=>value;
source='const {parseRiskView,parseSystemStatus,parseMarketSnapshot,parseWorldState}=globalThis;\n'+source;
const api=await import(encode(source));

test('risk read requests the selected configured instrument and validates the same identity',async()=>{
 const previous=globalThis.fetch,calls=[];
 try{
  globalThis.fetch=async url=>{calls.push(String(url));return new Response(JSON.stringify({instrument_id:'USDJPY'}),{status:200});};
  const result=await api.getRisk('USDJPY');
  assert.equal(calls[0].endsWith('/api/v1/risk?instrument=USDJPY'),true);
  assert.deepEqual(result,{ok:true,value:{instrument_id:'USDJPY'}});
 }finally{globalThis.fetch=previous;delete globalThis.parseRiskView;delete globalThis.parseSystemStatus;delete globalThis.parseMarketSnapshot;delete globalThis.parseWorldState;}
});

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const require=createRequire(import.meta.url);
const compile=(source,jsx=false)=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:jsx?ts.JsxEmit.ReactJSX:undefined}}).outputText.replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))).toString('base64')}`;
const fixture=JSON.parse(readFileSync(new URL('./fixtures/review-spending.json',import.meta.url),'utf8'));
async function modules(){
 const path=new URL('../src/features/costs/review-spending.ts',import.meta.url);assert(existsSync(path),'Protected spending parser must exist');
 const url=compile(readFileSync(path,'utf8')),parser=await import(url);
 const panelPath=new URL('../src/features/costs/review-spending-panel.tsx',import.meta.url);assert(existsSync(panelPath),'Spending panel must exist');
 return {...parser,...await import(compile(readFileSync(panelPath,'utf8').replace('from "./review-spending"',`from "${url}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve('next/link')).href)),true))};
}
test('native protected observation preserves all exact limits and unsettled values without private fields',async()=>{
 const m=await modules();assert.deepEqual(m.parseReviewSpending({...fixture,credential:'private',agents:fixture.agents.map(row=>({...row,path:'private'}))}),fixture);
 assert.equal(m.formatNanoUSD('9007199254740993'),'$9007199.254740993');assert.equal(m.formatNanoUSD('1224'),'$0.000001224');
});
test('rejects incomplete roles, numerical rounding, unsupported accounting and inconsistent ceilings',async()=>{
 const {parseReviewSpending:parse}=await modules();
 const badRow=changes=>({...fixture,agents:fixture.agents.map((row,i)=>i===1?{...row,...changes}:row)});
 for(const value of [{...fixture,mode:'LIVE'},{...fixture,billable_cost_accounting:true},{...fixture,recovery_spending_enabled:true},{...fixture,agents:fixture.agents.slice(1)},badRow({daily_nano_usd:1224}),badRow({daily_money_remaining:'98777'}),badRow({pending_count:0}),badRow({unsettled_tokens:229}),badRow({daily_token_remaining:673}),badRow({daily_token_limit:true}),{...fixture,observed_at:'2026-02-30T00:00:00Z'},{...fixture,day:'2026-10-11'},{...fixture,model:'moving-alias'},{...fixture,agents:[fixture.agents[0],...fixture.agents.slice(0,17)]}])assert.equal(parse(value),null);
});
test('unavailable and unconfigured observations never infer usage or remaining capacity',async()=>{
 const {parseReviewSpending:parse}=await modules();
 for(const state of ['UNAVAILABLE','NOT_CONFIGURED']){
  const value={...fixture,state,day:null,month:null,model:null,policy_sha256:null,head_hash:null,agents:[]};assert.deepEqual(parse(value),value);
  assert.equal(parse({...value,agents:fixture.agents}),null);
 }
});
test('cost view explains original holds and declared rates with exact amounts and a real refresh',async()=>{
 const {ReviewSpendingPanel}=await modules();const html=renderToStaticMarkup(React.createElement(ReviewSpendingPanel,{result:{ok:true,value:fixture}}));
 assert.match(html,/\$0\.000001224/);
 assert.match(html,/Unsettled requests/);assert.match(html,/Protected reserve/);assert.match(html,/not a provider invoice/);assert.match(html,/2026-10-10/);assert.match(html,/<form(?=[^>]*method="get")(?=[^>]*action="\/costs")[^>]*>/);assert.match(html,/<button[^>]*type="submit"[^>]*>Refresh spending snapshot<\/button>/);assert.equal((html.match(/scope="row"/g)||[]).length,36);assert.doesNotMatch(html,/fixture-key|private/);
});
test('sign-in and unavailable states omit tables; breached verified data remains labelled blocked',async()=>{
 const {ReviewSpendingPanel}=await modules();
 for(const result of [{ok:false,reason:'SIGN_IN'},{ok:false,reason:'UNAVAILABLE'},{ok:true,value:{...fixture,agents:[]}}]){
  const html=renderToStaticMarkup(React.createElement(ReviewSpendingPanel,{result}));assert.doesNotMatch(html,/<tbody>/);assert.match(html,/role="status"/);
 }
 const html=renderToStaticMarkup(React.createElement(ReviewSpendingPanel,{result:{ok:true,value:{...fixture,state:'BREACHED'}}}));assert.match(html,/Generation blocked/);assert.match(html,/BREACHED/);
});
test('breached amounts above native number precision stay exact through validation and rendering',async()=>{
 const m=await modules(),value={...fixture,state:'BREACHED',agents:fixture.agents.map((row,i)=>i===1?{...row,daily_tokens:2000000000,monthly_tokens:2000000000,unsettled_tokens:0,pending_count:0,daily_token_remaining:0,monthly_token_remaining:0,unsettled_nano_usd:'0',daily_nano_usd:'2000000000000000000',monthly_nano_usd:'2000000000000000000',daily_money_remaining:'0',monthly_money_remaining:'0'}:row)};
 assert.equal(m.parseReviewSpending(value).agents[1].daily_nano_usd,'2000000000000000000');
 const html=renderToStaticMarkup(React.createElement(m.ReviewSpendingPanel,{result:{ok:true,value}}));assert.match(html,/\$2000000000\.000000000/);assert.match(html,/Generation blocked/);
});

import {test} from "node:test";
import assert from "node:assert/strict";
import {existsSync,readFileSync} from "node:fs";
import ts from "typescript";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {createRequire} from "node:module";
import {pathToFileURL} from "node:url";
const require=createRequire(import.meta.url);
const compile=(source,jsx=false)=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:jsx?ts.JsxEmit.ReactJSX:undefined}}).outputText.replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))).toString("base64")}`;
const read=name=>readFileSync(new URL(`../src/features/costs/${name}`,import.meta.url),"utf8");
const fixture=JSON.parse(readFileSync(new URL("./fixtures/shared-spending.json",import.meta.url),"utf8"));
const legacy=JSON.parse(readFileSync(new URL("./fixtures/review-spending.json",import.meta.url),"utf8"));
async function modules(){
 assert(existsSync(new URL("../src/features/costs/shared-spending.ts",import.meta.url)),"Shared original-book parser must exist");
 const counterURL=compile(read("review-spending.ts"));
 const parserURL=compile(read("shared-spending.ts").replace('from "./review-spending"',`from "${counterURL}"`));
 const tableURL=compile(read("spending-tables.tsx").replace('from "./review-spending"',`from "${counterURL}"`),true);
 const reviewURL=compile(read("review-spending-panel.tsx").replace('from "./review-spending"',`from "${counterURL}"`).replace('from "./spending-tables"',`from "${tableURL}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href)),true);
 const panelURL=compile(read("shared-spending-panel.tsx").replace('from "./shared-spending"',`from "${parserURL}"`).replace('from "./spending-tables"',`from "${tableURL}"`).replace('from "./review-spending-panel"',`from "${reviewURL}"`).replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href)),true);
 return {...await import(parserURL),...await import(panelURL)};
}
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));

test("native shared snapshot preserves one book, exact amounts and both model accounting contracts",async()=>{
 const {parseSharedSpending:parse}=await modules();
 assert.deepEqual(parse({...fixture,credential:"private",models:fixture.models.map(model=>({...model,path:"private",agents:model.agents.map(row=>({...row,credential:"private"}))}))}),fixture);
 assert.equal(parse(fixture).agents[1].daily_nano_usd,"2752872");
});

test("rejects coherent but false aggregate totals, source mismatches and invalid model inventories",async()=>{
 const {parseSharedSpending:parse}=await modules(),child=structuredClone(fixture);
 const row=child.models[0].agents[1];row.daily_tokens++;row.monthly_tokens++;row.daily_token_remaining--;row.monthly_token_remaining--;
 for(const value of [child,{...fixture,invoice_verified:true},{...fixture,models:fixture.models.slice(1)}, {...fixture,models:[fixture.models[0],fixture.models[0]]},{...fixture,routes:fixture.routes.map(route=>({...route,purpose:"REVIEW"}))},{...fixture,routes:[{...fixture.routes[0],model:"moving-alias"}]},{...fixture,book_id:"invalid"},{...fixture,models:fixture.models.map(model=>({...model,accounting:"unmeasured"}))},{...fixture,day:"2026-10-12"},{...fixture,observed_at:"2026-02-30T00:00:00Z"},{...fixture,agents:fixture.agents.map((row,i)=>i===1?{...row,daily_nano_usd:2752872}:row)}])assert.equal(parse(value),null);
});

test("empty source states never imply a zero bill or remaining capacity",async()=>{
 const {parseSharedSpending:parse,SharedSpendingPanel}=await modules();
 for(const state of ["NOT_CONFIGURED","NOT_SHARED","SEPARATE_LEDGERS","INCOMPLETE_CONFIGURATION","UNAVAILABLE"]){
  const value={...fixture,state,book_id:null,day:null,month:null,policy_sha256:null,head_hash:null,agents:[],models:[],routes:[]};
  assert.deepEqual(parse(value),value);assert.equal(parse({...value,models:fixture.models}),null);
  const html=render(SharedSpendingPanel,{result:{ok:true,value}});assert.doesNotMatch(html,/<tbody>/);assert.match(html,/role="status"/);
 }
});

test("Costs renders aggregate once and separate per-model drilldowns without the duplicated legacy panel",async()=>{
 const {SpendingPanels}=await modules();
 const html=render(SpendingPanels,{sharedResult:{ok:true,value:fixture},reviewResult:{ok:true,value:legacy}});
 assert.match(html,/Shared provider spending capacity/);assert.match(html,/Counted tokens/);assert.match(html,/Input tokens only/);assert.match(html,/Input and output tokens/);assert.match(html,/one original ledger snapshot/);assert.match(html,/not a provider invoice/);
 assert.doesNotMatch(html,/OpenAI review spending capacity/);assert.equal((html.match(/scope="row"/g)||[]).length,108);
 assert.match(html,/<summary>[^<]*gpt-/);assert.match(html,/<summary>[^<]*jev-/);
 assert.match(html,/\$0\.002752872/);assert.match(html,/<form(?=[^>]*method="get")(?=[^>]*action="\/costs")[^>]*>/);
 assert.doesNotMatch(html,/fixture-key|private|reset|onclick=/i);
});

test("separate-book and sign-in states retain honest legacy support without a combined total",async()=>{
 const {SpendingPanels}=await modules();
 const empty={...fixture,state:"SEPARATE_LEDGERS",book_id:null,day:null,month:null,policy_sha256:null,head_hash:null,agents:[],models:[],routes:[]};
 const html=render(SpendingPanels,{sharedResult:{ok:true,value:empty},reviewResult:{ok:true,value:legacy}});
 assert.match(html,/separate ledgers/);assert.match(html,/OpenAI review spending capacity/);assert.equal((html.match(/scope="row"/g)||[]).length,36);
 const signIn=render(SpendingPanels,{sharedResult:{ok:false,reason:"SIGN_IN"},reviewResult:{ok:false,reason:"SIGN_IN"}});assert.doesNotMatch(signIn,/<tbody>/);assert.match(signIn,/href="\/risk"/);
});

test("breached shared amounts above number precision remain exact across aggregate and model views",async()=>{
 const {parseSharedSpending:parse,SpendingPanels}=await modules();
 const value=structuredClone(fixture);value.state="BREACHED";
 const child=value.models[0].agents[1];
 Object.assign(child,{daily_tokens:2000000000,monthly_tokens:2000000000,daily_token_remaining:0,monthly_token_remaining:0,daily_nano_usd:"2000000000000000000",monthly_nano_usd:"2000000000000000000",daily_money_remaining:"0",monthly_money_remaining:"0"});
 Object.assign(value.agents[1],{daily_tokens:2000065536,monthly_tokens:2000065536,daily_token_remaining:0,monthly_token_remaining:0,daily_nano_usd:"2000000000002752512",monthly_nano_usd:"2000000000002752512",daily_money_remaining:"0",monthly_money_remaining:"0"});
 assert.equal(parse(value).agents[1].daily_nano_usd,"2000000000002752512");
 const html=render(SpendingPanels,{sharedResult:{ok:true,value},reviewResult:{ok:true,value:legacy}});
 assert.match(html,/Generation blocked across this shared ledger/);assert.match(html,/\$2000000000\.002752512/);assert.doesNotMatch(html,/OpenAI review spending capacity/);
});

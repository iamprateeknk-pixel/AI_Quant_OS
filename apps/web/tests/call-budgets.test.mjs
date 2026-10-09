import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const source = readFileSync(new URL('../src/features/costs/call-budgets.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { parseCallBudget } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const require = createRequire(import.meta.url);
const parserUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`;
const panelSource = readFileSync(new URL('../src/features/costs/call-budget-panel.tsx',import.meta.url),'utf8').replace('import Link from "next/link";', 'const Link = globalThis.budgetTestLink;').replace('from "./call-budgets"', `from "${parserUrl}"`);
const panelCompiled = ts.transpileModule(panelSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText.replace('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href));
globalThis.budgetTestLink = ({children,...props}) => React.createElement('a',props,children);
const {CallBudgetPanel} = await import(`data:text/javascript;base64,${Buffer.from(panelCompiled).toString('base64')}`);
delete globalThis.budgetTestLink;
const rows = Array.from({length:18}, (_, i) => ({agent_id:`A${String(i+1).padStart(2,'0')}`,daily_calls:2,monthly_calls:5,daily_remaining:8,monthly_remaining:15,daily_recovery_reserve:1,monthly_recovery_reserve:3}));
const base = {schema_version:'1.0',mode:'PAPER',state:'ENFORCED',accounting_basis:'RESERVED_ROUTINE_TOOL_CALL_ATTEMPTS',recovery_spending_enabled:false,provider_token_accounting:false,billable_cost_accounting:false,day:'2026-10-09',month:'2026-10',head_hash:'a'.repeat(64),policy_sha256:'b'.repeat(64),agents:rows};
test('retains all role counters and projects only observation fields', () => {
  assert.deepEqual(parseCallBudget({...base,operator_token:'private',agents:rows.map(row=>({...row,prompt:'private'}))}),base);
});
test('distinguishes disabled and unavailable without fabricated counters', () => {
  for (const state of ['DISABLED','UNAVAILABLE']) {
    const value={...base,state,day:null,month:null,head_hash:null,policy_sha256:null,agents:[]};
    assert.deepEqual(parseCallBudget(value),value);
    assert.equal(parseCallBudget({...value,agents:rows}),null);
    assert.equal(parseCallBudget({...value,day:base.day}),null);
  }
});
test('rejects incomplete roles, invalid counters and unsupported accounting claims', () => {
  for (const value of [{...base,mode:'LIVE'},{...base,agents:rows.slice(1)},{...base,agents:[rows[0],...rows.slice(0,17)]},{...base,agents:rows.map(row=>({...row,daily_calls:-1}))},{...base,agents:rows.map(row=>({...row,monthly_calls:true}))},{...base,agents:rows.map(row=>({...row,daily_remaining:100001}))},{...base,billable_cost_accounting:true},{...base,provider_token_accounting:true},{...base,recovery_spending_enabled:true}]) assert.equal(parseCallBudget(value),null);
});
test('rejects inconsistent periods, impossible dates and malformed provenance', () => {
  for(const value of [{...base,month:'2026-09'},{...base,day:'2026-02-30',month:'2026-02'},{...base,day:'2026-13-01',month:'2026-13'},{...base,head_hash:'missing'},{...base,policy_sha256:null},{...base,agents:rows.map(row=>({...row,daily_calls:6,monthly_calls:5}))}]) assert.equal(parseCallBudget(value),null);
});
test('renders verified daily/monthly attempts and protects accounting meaning',()=>{
  const html=renderToStaticMarkup(React.createElement(CallBudgetPanel,{result:{ok:true,value:{...base,operator_token:'private',agents:rows.map(row=>({...row,daily_remaining:0,prompt:'private'}))}}}));
  assert.equal((html.match(/scope="row"/g)||[]).length,18);
  assert.match(html,/Daily recovery reserve/);
  assert.match(html,/Monthly recovery reserve/);
  assert.match(html,/2026-10-09/);
  assert.match(html,/Exhausted/);
  assert.match(html,/Failed or interrupted attempts remain counted/);
  assert.doesNotMatch(html,/private|\$0/);
});
test('renders sign-in, disabled and unavailable without usage rows',()=>{
  const inputs=[{ok:false,reason:'SIGN_IN'},{ok:false,reason:'UNAVAILABLE'},{ok:true,value:{...base,state:'DISABLED',day:null,month:null,head_hash:null,policy_sha256:null,agents:[]}},{ok:true,value:{...base,state:'UNAVAILABLE',day:null,month:null,head_hash:null,policy_sha256:null,agents:[]}},{ok:true,value:{...base,agents:[]}}];
  for (const result of inputs) {
    const html=renderToStaticMarkup(React.createElement(CallBudgetPanel,{result}));
    assert.doesNotMatch(html,/<tbody>|scope="row"/);
    assert.match(html,/role="status"/);
    if (!result.ok && result.reason==='SIGN_IN') assert.match(html,/href="\/risk"/);
    else assert.match(html,/href="\/costs"/);
  }
});

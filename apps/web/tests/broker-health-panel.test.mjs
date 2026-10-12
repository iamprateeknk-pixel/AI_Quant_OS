import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const require=createRequire(import.meta.url);
function compile(source){return ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;}
const parser=`data:text/javascript;base64,${Buffer.from(compile(readFileSync(new URL('../src/features/brokers/health.ts',import.meta.url),'utf8'))).toString('base64')}`;
globalThis.healthLink=({children,...props})=>React.createElement('a',props,children);
const source=readFileSync(new URL('../src/features/brokers/health-panel.tsx',import.meta.url),'utf8').replace('import Link from "next/link";','const Link=globalThis.healthLink;').replace('from "./health"',`from "${parser}"`);
const {BrokerHealthPanel}=await import(`data:text/javascript;base64,${Buffer.from(compile(source).replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))).toString('base64')}`);
delete globalThis.healthLink;
const ids=['PAPER','EXNESS_MT5_DEMO','EXNESS','IBKR','ZERODHA'];
const payload={schema_version:'1.0',mode:'PAPER',live_execution_released:false,brokers:ids.map(broker_id=>({schema_version:'1.0',mode:'PAPER',broker_id,connection_state:'UNKNOWN',quote_age_ms:null,order_latency_ms:null,reject_rate:null,reconciliation_state:'UNASSESSED',spread_state:'UNASSESSED',account_state:'UNASSESSED',health_score:null,updated_at:'2026-10-09T12:00:00Z',live_execution_released:false}))};
const render=result=>renderToStaticMarkup(React.createElement(BrokerHealthPanel,{result,observedAt:'2026-10-09T12:00:01Z'}));
test('health panel shows five unknown broker observations without inventing a score',()=>{
 const html=render({ok:true,value:payload});assert(html.includes('Broker health'));assert(html.includes('Unknown'));assert(html.includes('Unassessed'));assert(html.includes('No score'));assert(html.includes('Live execution remains unreleased'));
});
test('health panel distinguishes sign-in, unavailable and invalid evidence',()=>{
 assert(render({ok:false,reason:'SIGN_IN'}).includes('Sign in at Risk Center'));
 assert(render({ok:false,reason:'UNAVAILABLE'}).includes('unavailable'));
 assert(render({ok:true,value:{...payload,live_execution_released:true}}).includes('unavailable'));
});

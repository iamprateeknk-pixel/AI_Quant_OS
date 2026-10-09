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
const parser=`data:text/javascript;base64,${Buffer.from(compile(readFileSync(new URL('../src/features/brokers/capabilities.ts',import.meta.url),'utf8'))).toString('base64')}`;
globalThis.brokerLink=({children,...props})=>React.createElement('a',props,children);
const source=readFileSync(new URL('../src/features/brokers/capability-panel.tsx',import.meta.url),'utf8').replace('import Link from "next/link";','const Link=globalThis.brokerLink;').replace('from "./capabilities"',`from "${parser}"`);
const {BrokerCapabilityPanel}=await import(`data:text/javascript;base64,${Buffer.from(compile(source).replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))).toString('base64')}`);
delete globalThis.brokerLink;
const fixture=JSON.parse(readFileSync(new URL('./fixtures/broker-capabilities.json',import.meta.url),'utf8'));
const render=result=>renderToStaticMarkup(React.createElement(BrokerCapabilityPanel,{result,observedAt:'2026-10-09T12:00:00Z'}));
test('broker capability table separates simulator, observations and unsupported execution',()=>{
 const html=render({ok:true,value:{...fixture,password:'fixture-private'}});
 assert(html.includes('Paper simulator'));assert(html.includes('Demo observations'));assert(html.includes('Unsupported skeleton'));
 assert(html.includes('Interactive Brokers'));assert(html.includes('Zerodha'));assert(html.includes('Read account'));
 assert(html.includes('Native execution remains unreleased'));assert(!html.includes('fixture-private'));assert(!html.includes('ai_quant_os'));
 assert(html.includes('scope="col"'));assert(html.includes('<caption'));assert(!html.includes('<button'));
});
test('capability table has sign-in and unavailable states without fabricated rows',()=>{
 assert(render({ok:false,reason:'SIGN_IN'}).includes('Sign in at Risk Center'));
 assert(render({ok:false,reason:'UNAVAILABLE'}).includes('unavailable'));
 assert(render({ok:true,value:{...fixture,live_execution_released:true}}).includes('unavailable'));
});

test('paper eligibility denial and absent mappings remain explicit',()=>{
 const rows=fixture.brokers.map(row=>row.broker_id==='PAPER'?{...row,eligible:false,compliance_scope:'DENIED',paper_admission_policy_allows:false,instruments:[]}:row);
 const html=render({ok:true,value:{...fixture,brokers:rows}});
 assert(html.includes('Denied'));assert(html.includes('Paper broker gate blocks'));assert(html.includes('None mapped'));
});

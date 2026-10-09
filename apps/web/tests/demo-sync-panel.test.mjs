import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {now,payload,record,health} from './fixtures/demo-sync.mjs';
const require=createRequire(import.meta.url);
function compile(source){return ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;}
const parser=`data:text/javascript;base64,${Buffer.from(compile(readFileSync(new URL('../src/features/brokers/demo-sync-health.ts',import.meta.url),'utf8'))).toString('base64')}`;
globalThis.demoLink=({children,...props})=>React.createElement('a',props,children);
const source=readFileSync(new URL('../src/features/brokers/demo-sync-panel.tsx',import.meta.url),'utf8').replace('import Link from "next/link";','const Link=globalThis.demoLink;').replace('from "./demo-sync-health"',`from "${parser}"`);
const {DemoSyncPanel}=await import(`data:text/javascript;base64,${Buffer.from(compile(source).replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))).toString('base64')}`);
delete globalThis.demoLink;
const render=(result,observedAt=now)=>renderToStaticMarkup(React.createElement(DemoSyncPanel,{result,observedAt}));
test('demo panel shows recorded state without credentials or native readiness claims',()=>{
 const html=render({ok:true,value:{...payload,password:'fixture-private-password'}});
 assert(html.includes('Demo account synchronization'));assert(html.includes('Running'));assert(html.includes('Last sync succeeded'));
 assert(html.includes('Native account freshness remains unverified'));assert(!html.includes('fixture-private-password'));assert(!html.includes('Connected'));
 assert(render({ok:true,value:payload},'2026-10-09T12:00:31Z').includes('Record is stale'));
});
test('demo panel distinguishes sign-in, unavailable and not configured',()=>{
 assert(render({ok:false,reason:'SIGN_IN'}).includes('Sign in at Risk Center'));
 assert(render({ok:false,reason:'UNAVAILABLE'}).includes('unavailable'));
 assert(render({ok:true,value:{...payload,configured:false,observation:{...payload.observation,state:'UNAVAILABLE',record:null}}}).includes('not configured'));
});
test('halted pending capture remains visible with original observation times',()=>{
 const attempt='9aaec2df-d4b1-4670-8f57-f3ead7867be2';
 const html=render({ok:true,value:{...payload,observation:{...payload.observation,record:{...record,state:'HALTED',health:{...health,state:'HALTED',code:'CUSTODY_UNCERTAIN',pending_attempt_id:attempt,pending_history_start:'2026-10-09T11:00:00Z',pending_history_end:now}}}}});
 assert(html.includes('Worker custody is uncertain'));assert(html.includes(attempt));assert(html.includes('Automatic observations halted'));
});

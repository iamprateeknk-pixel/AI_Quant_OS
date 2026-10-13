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
const parser=`data:text/javascript;base64,${Buffer.from(compile(readFileSync(new URL('../src/features/orders/reconciliation.ts',import.meta.url),'utf8'))).toString('base64')}`;
const source=readFileSync(new URL('../src/features/orders/reconciliation-panel.tsx',import.meta.url),'utf8').replace('import {useRouter} from "next/navigation";','const useRouter=()=>({refresh:()=>{}});').replace('import Link from "next/link";','const Link=({href,children})=>globalThis.React.createElement("a",{href},children);').replace('from "./reconciliation"',`from "${parser}"`);
globalThis.React=React;
const component=compile(source).replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href)).replace('"react"',JSON.stringify(pathToFileURL(require.resolve('react')).href));
const {PaperReconciliationPanel}=await import(`data:text/javascript;base64,${Buffer.from(component).toString('base64')}`);
test('reconciliation panel requires explicit confirmation and explains safety consequence before the action',()=>{
 const html=renderToStaticMarkup(React.createElement(PaperReconciliationPanel));
 assert(html.includes('Paper fill ledger reconciliation'));assert(html.includes('comparison is audited'));
 assert(html.includes('may stop new paper risk'));assert.match(html,/<input[^>]*type="checkbox"/);
 assert.match(html,/<button type="submit" disabled="">Compare paper ledgers<\/button>/);
});

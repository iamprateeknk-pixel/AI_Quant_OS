import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const require=createRequire(import.meta.url),path=new URL('../src/features/portfolio/demo-portfolio-panel.tsx',import.meta.url);
const payload=JSON.parse(readFileSync(new URL('./fixtures/demo-portfolio.json',import.meta.url),'utf8')),now=payload.observation.publication.record.observed_at;
function compile(source){return ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;}
async function panel(){
 assert(existsSync(path),'Demo portfolio panel must exist');
 const jsxRuntime=JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href);
 const parser=`data:text/javascript;base64,${Buffer.from(compile(readFileSync(new URL('../src/features/portfolio/demo-portfolio.ts',import.meta.url),'utf8'))).toString('base64')}`;
 const scrollSource=readFileSync(new URL('../src/features/shared/table-scroll-region.tsx',import.meta.url),'utf8');
 const scrollRegion=`data:text/javascript;base64,${Buffer.from(compile(scrollSource).replace('"react/jsx-runtime"',jsxRuntime)).toString('base64')}`;
 const source=readFileSync(path,'utf8').replace('from "./demo-portfolio"',`from "${parser}"`).replace('from "../shared/table-scroll-region"',`from "${scrollRegion}"`);
 return (await import(`data:text/javascript;base64,${Buffer.from(compile(source).replace('"react/jsx-runtime"',jsxRuntime)).toString('base64')}`)).DemoPortfolioPanel;
}
async function render(result,observedAt=now){return renderToStaticMarkup(React.createElement(await panel(),{result,observedAt}));}
test('account display includes currency, original source times, positions and pending orders without trade controls',async()=>{
 const value=structuredClone(payload);value.password='fixture-private-password';const html=await render({ok:true,value});
 for(const text of ['Demo account snapshot','10000.0 USD','Observed, UTC','2026-10-09T12:00:03Z','EURUSDm','Pending demo orders','Native account freshness remains unverified','Read only'])assert(html.includes(text));
 assert(!html.includes('fixture-private-password'));assert(!html.includes('<button'));assert(html.includes('href="/portfolio"'));
});
test('saved positions and pending orders tables are named keyboard-scroll regions',async()=>{
 const html=await render({ok:true,value:payload});
 for(const label of ['Saved demo positions table','Pending demo orders table']){
  assert.match(html,new RegExp(`<div class="table-scroll" role="region" aria-label="${label}" tabindex="0">`));
 }
});
test('expired data keeps saved amounts and exposes expiry rather than claiming a current account',async()=>{
 const html=await render({ok:true,value:payload},'2026-10-09T12:00:04Z');assert(html.includes('Snapshot expired'));assert(html.includes('10000.0 USD'));assert(html.includes('Current account state is unknown'));
});
test('sign-in, unavailable, empty and unconfigured states do not render financial zeroes',async()=>{
 assert((await render({ok:false,reason:'SIGN_IN'})).includes('href="/risk"'));assert(!(await render({ok:false,reason:'UNAVAILABLE'})).includes('10000.0'));
 const value=structuredClone(payload);value.observation.state='UNAVAILABLE';value.observation.publication={...value.observation.publication,state:'EMPTY',record:null};assert((await render({ok:true,value})).includes('No demo account snapshot has been saved'));
 value.configured=false;value.observation.publication=null;assert((await render({ok:true,value})).includes('not configured'));
});

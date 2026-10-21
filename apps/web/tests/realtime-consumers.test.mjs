import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const require=createRequire(import.meta.url);
globalThis.tableScrollRegion=({label,children})=>React.createElement('div',{className:'table-scroll',role:'region','aria-label':label,tabIndex:0},children);
const compile=source=>ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
const url=source=>`data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`;
const fixture=JSON.parse(readFileSync(new URL('./fixtures/realtime-snapshot.json',import.meta.url),'utf8'));
const frame={epoch:fixture.epoch,sequence:1,captured_at:fixture.captured_at,valid_until:fixture.valid_until,safety_state:'HALT',order_count:1,world_version:fixture.world.world_state_version,world_status:fixture.world.status,instruments:fixture.world.instruments,currency_twins:fixture.world.currency_twins};
async function component(path,name,view){
 assert(existsSync(path),'Realtime consumer must exist');
 const hook=url('export function useRealtime(){return '+JSON.stringify(view)+';}');
 const source=readFileSync(path,'utf8').replace(/from "(?:\.\/|@\/features\/realtime\/)use-realtime"/g,`from "${hook}"`).replace(/import \{\s*TableScrollRegion\s*\} from "(?:\.\.\/shared|@\/features\/shared)\/table-scroll-region";/g,'const TableScrollRegion=globalThis.tableScrollRegion;');
 const code=compile(source).replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href));
 return renderToStaticMarkup(React.createElement((await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`))[name]));
}
test('persistent header exposes current authoritative safety and withholds historical safety',async()=>{
 const path=new URL('../src/features/realtime/realtime-header.tsx',import.meta.url);
 assert((await component(path,'RealtimeHeader',{state:'CURRENT',frame})).includes('PAPER · HALT'));
 const stale=await component(path,'RealtimeHeader',{state:'STALE',frame});assert(stale.includes('CURRENT STATE UNKNOWN'));assert(!stale.includes('PAPER · HALT'));
});
test('World State uses current exact features and labels retained data historical',async()=>{
 const path=new URL('../src/features/world-state/world-state-panel.tsx',import.meta.url),current=structuredClone(frame);current.instruments[0].return_bps='-12.345678901234567890';
 const html=await component(path,'WorldStatePanel',{state:'CURRENT',frame:current});assert(html.includes('-12.345678901234567890'));assert(html.includes('Currency coverage'));assert(html.includes('Updates automatically'));assert(html.includes('aria-label="Paper regime and instrument evidence table"'));assert(html.includes('aria-label="Paper currency coverage table"'));assert(!html.includes('Reload for the latest'));
 const expired=await component(path,'WorldStatePanel',{state:'STALE',frame:current});assert(expired.includes('Current world state is unknown'));assert(expired.includes('Last accepted simulated evidence'));
 const empty=await component(path,'WorldStatePanel',{state:'CONNECTING',frame:null});assert(empty.includes('No current paper observation'));assert(!empty.includes('-12.345678901234567890'));
});

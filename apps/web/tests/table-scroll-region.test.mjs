import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';

const require=createRequire(import.meta.url);
const source=readFileSync(new URL('../src/features/shared/table-scroll-region.tsx',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText
 .replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href));
const {TableScrollRegion}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const region=TableScrollRegion({label:'Orders table',children:'table'});
function keyEvent(key){
 const calls=[];let prevented=false;
 const event={key,currentTarget:{scrollBy:options=>calls.push(options)},preventDefault:()=>{prevented=true}};
 region.props.onKeyDown(event);
 return {calls,prevented};
}

test('table scroll region exposes a named, keyboard-focusable landmark',()=>{
 assert.equal(region.props.className,'table-scroll');
 assert.equal(region.props.role,'region');
 assert.equal(region.props['aria-label'],'Orders table');
 assert.equal(region.props.tabIndex,0);
});

test('left and right arrows scroll the table region and keep the page stationary',()=>{
 assert.deepEqual(keyEvent('ArrowRight'),{calls:[{left:96,behavior:'smooth'}],prevented:true});
 assert.deepEqual(keyEvent('ArrowLeft'),{calls:[{left:-96,behavior:'smooth'}],prevented:true});
});

test('vertical and unrelated keys keep their normal browser behavior',()=>{
 assert.deepEqual(keyEvent('ArrowDown'),{calls:[],prevented:false});
 assert.deepEqual(keyEvent('Tab'),{calls:[],prevented:false});
});

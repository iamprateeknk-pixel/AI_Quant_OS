import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../src/features/dashboard/metrics.ts',import.meta.url),'utf8');
const {countOpenPositions}=await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`);
test('position count distinguishes signed decimal quantities from exact zero without floating point',()=>{
 assert.equal(countOpenPositions([{net_quantity:'0.0000'},{net_quantity:'-1.25'},{net_quantity:'3e-8'},{net_quantity:'0'}]),2);
 assert.equal(countOpenPositions([]),0);
});
test('position count withholds malformed or incomplete quantities',()=>{
 assert.equal(countOpenPositions([{net_quantity:'NaN'}]),null);
 assert.equal(countOpenPositions([{quantity:'1'}]),null);
 assert.equal(countOpenPositions(null),null);
});

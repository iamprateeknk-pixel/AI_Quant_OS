import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import ts from "typescript";

const source=readFileSync(new URL("../src/lib/navigation.ts",import.meta.url),"utf8");
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const moduleUrl=`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {isActiveNavigationItem,searchNavigationItems}=await import(moduleUrl);

test("primary navigation marks exact destinations without prefix collisions",()=>{
 assert.equal(isActiveNavigationItem("/risk","/risk"),true);
 assert.equal(isActiveNavigationItem("/risk","/research"),false);
 assert.equal(isActiveNavigationItem("/researcher","/research"),false);
 assert.equal(isActiveNavigationItem("/","/"),true);
 assert.equal(isActiveNavigationItem("/world-state","/"),false);
});

test("nested operational routes keep their owning navigation destination active",()=>{
 assert.equal(isActiveNavigationItem("/traces/abc-123","/traces"),true);
 assert.equal(isActiveNavigationItem("/incidents/runbook","/incidents"),true);
 assert.equal(isActiveNavigationItem("/markets/GBPUSD","/markets/EURUSD"),true);
});

test("command search finds route labels and groups without changing destinations",()=>{
 assert.deepEqual(searchNavigationItems("trading").map(item=>item.label),["Brokers","Strategies","Agents","Risk","Portfolio","Orders"]);
 assert.deepEqual(searchNavigationItems("  audit ").map(item=>item.href),["/audit"]);
 assert.equal(searchNavigationItems("no matching destination").length,0);
 assert.equal(searchNavigationItems(" ").length,16);
});

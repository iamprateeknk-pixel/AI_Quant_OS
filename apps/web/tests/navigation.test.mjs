import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import {pathToFileURL} from "node:url";
import ts from "typescript";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

const source=readFileSync(new URL("../src/lib/navigation.ts",import.meta.url),"utf8");
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const moduleUrl=`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {isActiveNavigationItem,searchNavigationItems}=await import(moduleUrl);
const require=createRequire(import.meta.url);
const componentSource=readFileSync(new URL("../src/features/navigation/navigation-command.tsx",import.meta.url),"utf8");
const componentCode=ts.transpileModule(componentSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText
 .replace('"react/jsx-runtime"',JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))
 .replace('"react"',JSON.stringify(pathToFileURL(require.resolve("react")).href))
 .replace('"next/link"',JSON.stringify(pathToFileURL(require.resolve("next/link")).href))
 .replace('"@/lib/navigation"',JSON.stringify(moduleUrl));
const {NavigationCommand}=await import(`data:text/javascript;base64,${Buffer.from(componentCode).toString("base64")}`);

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

test("destination search exposes a named dialog and announces result count",()=>{
 const html=renderToStaticMarkup(React.createElement(NavigationCommand));
 assert.match(html,/<button[^>]*aria-haspopup="dialog"[^>]*>.*Search destinations.*Ctrl K/);
 assert.match(html,/<dialog[^>]*aria-labelledby="navigation-command-title"/);
 assert.match(html,/<h2 id="navigation-command-title">Go to a destination<\/h2>/);
 assert.match(html,/<p[^>]*role="status"[^>]*aria-live="polite">16 destinations available<\/p>/);
 assert.match(html,/<label class="sr-only" for="navigation-command-query">Search destinations<\/label>/);
});

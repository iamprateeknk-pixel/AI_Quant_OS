import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import ts from "typescript";
const compile=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString("base64")}`;
const boundary=compile(readFileSync(new URL("../src/features/operator-session/boundary.ts",import.meta.url),"utf8"));
const source=readFileSync(new URL("../src/features/operator-session/server.ts",import.meta.url),"utf8").replace('import {parsePaperReconciliation} from "../orders/reconciliation";','').replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.sharedSpendCookies();').replace('import { NextResponse } from "next/server";','const NextResponse={};').replace('from "./boundary"',`from "${boundary}"`);
const server=await import(compile(source));
test("fixed shared read keeps cookies private and supports a bounded full model inventory",async()=>{
 const priorFetch=globalThis.fetch,priorCookies=globalThis.sharedSpendCookies,calls=[];
 try{
  globalThis.sharedSpendCookies=()=>({get:()=>undefined});globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response('{}');};
  assert.deepEqual(await server.readSharedSpending(),{ok:false,reason:"SIGN_IN"});assert.equal(calls.length,0);
  globalThis.sharedSpendCookies=()=>({get:()=>({value:"fixture-private-session"})});assert.equal((await server.readSharedSpending()).ok,true);
  assert.equal(new URL(calls[0].url).pathname,"/api/v1/providers/shared-spending");assert.equal(calls[0].options.headers["X-Operator-Session"],"fixture-private-session");assert.equal(calls[0].options.cache,"no-store");assert.equal(calls[0].options.redirect,"error");assert(calls[0].options.signal instanceof AbortSignal);
  globalThis.fetch=async()=>new Response('{}'+' '.repeat(140000));assert.equal((await server.readSharedSpending()).ok,true);
  assert.deepEqual(await server.readReviewSpending(),{ok:false,reason:"UNAVAILABLE"});
  globalThis.fetch=async()=>new Response('x'.repeat(262145));assert.deepEqual(await server.readSharedSpending(),{ok:false,reason:"UNAVAILABLE"});
  globalThis.fetch=async()=>new Response(null,{status:401});assert.deepEqual(await server.readSharedSpending(),{ok:false,reason:"SIGN_IN"});
 }finally{globalThis.fetch=priorFetch;if(priorCookies===undefined)delete globalThis.sharedSpendCookies;else globalThis.sharedSpendCookies=priorCookies;}
});

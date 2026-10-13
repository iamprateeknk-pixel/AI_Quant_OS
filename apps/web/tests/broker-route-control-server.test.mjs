import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import ts from "typescript";
const moduleUrl=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString("base64")}`;
const boundary=moduleUrl(readFileSync(new URL("../src/features/operator-session/boundary.ts",import.meta.url),"utf8"));
const source=readFileSync(new URL("../src/features/operator-session/server.ts",import.meta.url),"utf8").replace('import {parsePaperReconciliation} from "../orders/reconciliation";','').replace('import "server-only";','').replace('import { cookies } from "next/headers";','const cookies=()=>globalThis.routeCookies();').replace('import { NextResponse } from "next/server";','const NextResponse={json:(body,options)=>({body,status:options.status,headers:options.headers})};').replace('from "./boundary"',`from "${boundary}"`);
const {submitBrokerRouteControl,brokerRouteControlReceipt}=await import(moduleUrl(source));
test("broker route control keeps credentials server-side and fixes the PAPER upstream path",async()=>{
 const oldFetch=globalThis.fetch,oldCookies=globalThis.routeCookies,oldApi=process.env.AI_QUANT_API_URL,oldOrigin=process.env.AI_QUANT_WEB_ORIGIN,calls=[];
 try{
  process.env.AI_QUANT_API_URL="http://127.0.0.1:8000";process.env.AI_QUANT_WEB_ORIGIN="http://127.0.0.1:3000";
  globalThis.routeCookies=()=>({get:()=>({value:"private-session"})});
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify({decision:"ACCEPTED"}));};
  const body={credential:"private-credential",action_id:"e2a563bb-560e-4334-8ed5-22a7984c9e73",requested_enabled:false,expected_enabled:true,reason:"Maintenance",operator_session_id:"a2a563bb-560e-4334-8ed5-22a7984c9e73",client_timestamp:"2026-10-13T00:00:00Z",confirmation:"DISABLE"};
  const request=(origin,value=body)=>new Request("http://127.0.0.1:3000/api/brokers/route-controls",{method:"POST",headers:{Host:"127.0.0.1:3000",Origin:origin,"Content-Type":"application/json"},body:JSON.stringify(value)});
  assert.equal((await submitBrokerRouteControl(request("https://evil.invalid"))).status,403);assert.equal(calls.length,0);
  const response=await submitBrokerRouteControl(request("http://127.0.0.1:3000"));assert.equal(response.status,200);
  assert.equal(new URL(calls[0].url).pathname,"/api/v1/brokers/PAPER/route-controls");assert.equal(calls[0].options.headers["X-Operator-Session"],"private-session");assert.equal(calls[0].options.headers["X-Operator-Reauth"],"private-credential");assert(!calls[0].options.body.includes("private-credential"));
  await brokerRouteControlReceipt(body.action_id);assert.equal(new URL(calls[1].url).pathname,"/api/v1/brokers/route-controls/"+body.action_id);
 }finally{globalThis.fetch=oldFetch;if(oldCookies===undefined)delete globalThis.routeCookies;else globalThis.routeCookies=oldCookies;for(const [key,value] of [["AI_QUANT_API_URL",oldApi],["AI_QUANT_WEB_ORIGIN",oldOrigin]])if(value===undefined)delete process.env[key];else process.env[key]=value;}
});

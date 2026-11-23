import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import ts from "typescript";

const compile=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString("base64")}`;
const agent={schema_version:"1.0",agent_id:"A02",agent_version:"0.2.0",name:"Regime analyst",squad:"Market",mission:"Classify paper regimes",output_type:"RegimeEvidence",required_inputs:["world_state"],allowed_tools:["read_world_state"],implementation_status:"PARTIAL",timeout_seconds:2,manifest_ref:`agent-manifest:${"a".repeat(64)}`,lifecycle:"SHADOW"};
const envelope={observed_at:"2026-10-12T00:00:00.000Z",registry:{ok:true,value:[agent]}};

test("agent read route validates session before fixed no-store registry reads",async()=>{
  const source=readFileSync(new URL("../src/app/api/agents/observations/route.ts",import.meta.url),"utf8");
  const bridge=compile("export const read=()=>globalThis.agentReads.registry();export const sessionStatus=()=>globalThis.agentReads.sessionStatus();export const parseRegistry=value=>globalThis.agentReads.parseRegistry(value);");
  const route=await import(compile(source.replace('from "@/lib/control-api"',`from "${bridge}"`).replace('from "@/features/operator-session/server"',`from "${bridge}"`).replace('from "@/features/agents/registry"',`from "${bridge}"`)));
  let reads=0;
  globalThis.agentReads={authorized:false,sessionStatus(){return Promise.resolve(new Response(null,{status:this.authorized?200:401}));},parseRegistry(){return [agent];},registry(){reads++;return Promise.resolve(envelope.registry);}};
  try{assert.equal((await route.GET(new Request("http://local/api/agents/observations?target=https://outside.invalid"))).status,422);assert.equal((await route.GET(new Request("http://local/api/agents/observations"))).status,401);assert.equal(reads,0);globalThis.agentReads.authorized=true;const response=await route.GET(new Request("http://local/api/agents/observations"));assert.equal(response.status,200);assert.equal(response.headers.get("cache-control"),"no-store");assert.deepEqual(Object.keys(await response.json()).sort(),["observed_at","registry"].sort());assert.equal(reads,1);}finally{delete globalThis.agentReads;}
});

test("agent browser reader validates bounded current registry envelopes",async()=>{
  const source=readFileSync(new URL("../src/features/agents/agents-live-read.ts",import.meta.url),"utf8").replace('import { parseRegistry, type AgentManifest } from "./registry";','const parseRegistry=globalThis.agentParsing.parseRegistry;');
  const registrySource=readFileSync(new URL("../src/features/agents/registry.ts",import.meta.url),"utf8"),registryModule=await import(compile(registrySource));
  globalThis.agentParsing={parseRegistry:registryModule.parseRegistry};
  const {parseAgentsSnapshot,readLiveAgents}=await import(compile(source));
  try{assert(parseAgentsSnapshot(envelope));assert.equal(parseAgentsSnapshot({...envelope,extra:true}),null);assert.equal(parseAgentsSnapshot({...envelope,registry:{ok:true,value:[{...agent,lifecycle:"READY"}]}}),null);const calls=[];globalThis.fetch=async(url,options)=>{calls.push([url,options]);return new Response(JSON.stringify(envelope));};const abort=new AbortController();assert.deepEqual(await readLiveAgents(abort.signal),envelope);assert.equal(calls[0][0],"/api/agents/observations");assert.equal(calls[0][1].cache,"no-store");assert.equal(calls[0][1].credentials,"same-origin");assert.equal(calls[0][1].signal,abort.signal);}finally{delete globalThis.fetch;delete globalThis.agentParsing;}
});

test("Agents page wires the public registry to its realtime panel",()=>{const page=readFileSync(new URL("../src/app/[section]/page.tsx",import.meta.url),"utf8");assert.match(page,/<AgentsLivePanel initialRead=\{agentRead\} \/>/);assert.doesNotMatch(page,/<AgentRegistryPanel \/>/);});

test("agent live store cancels pending reads and withholds data when the shared stream disconnects",async()=>{
  const path=new URL("../src/features/agents/agents-live-store.ts",import.meta.url);assert(existsSync(path),"Agent live store required");
  const {createAgentsLiveStore}=await import(compile(readFileSync(path,"utf8")));
  let now=Date.parse("2026-10-12T00:00:00Z"),sequence=0,signal={state:"CONNECTING",frame:null},id=0;
  const timers=new Map(),subscribers=new Set(),pending=[],reads=[];
  const store=createAgentsLiveStore({clock:()=>now,signal:()=>signal,subscribeTrigger(callback){subscribers.add(callback);return()=>subscribers.delete(callback);},read(abort){reads.push(abort);return new Promise(resolve=>pending.push(resolve));},schedule(callback,delay){const key=++id;timers.set(key,{callback,at:now+delay});return key;},cancel(key){timers.delete(key);}});
  const off=store.subscribe(()=>{});signal={state:"CURRENT",frame:{epoch:"12345678-1234-4123-8123-123456789abc",sequence:++sequence,captured_at:new Date(now).toISOString(),valid_until:new Date(now+3000).toISOString()}};for(const callback of subscribers)callback();assert.equal(reads.length,1);signal={state:"DISCONNECTED",frame:null};for(const callback of subscribers)callback();assert(reads[0].aborted);pending[0](structuredClone(envelope));for(let index=0;index<8;index++)await Promise.resolve();assert.equal(store.snapshot().snapshot,null);off();assert.equal(timers.size,0);
});

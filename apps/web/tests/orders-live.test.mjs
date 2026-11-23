import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import ts from "typescript";

const compile = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString("base64")}`;
const envelope = {
  observed_at: "2026-10-12T00:00:00.000Z",
  orders: { ok: true, value: { orders: [{ order_id:"o-1", intent_id:"i-1", risk_id:"r-1", instrument_id:"EURUSD", status:"FILLED", broker_route:"PAPER", broker_order_id:"paper-1" }] } },
  fills: { ok: true, value: { fills: [{ fill_id:"f-1", order_id:"o-1", instrument_id:"EURUSD", side:"BUY", quantity:"2", price:"1.1", fee:"0.01", observed_at:"2026-10-12T00:00:00Z", source:"PAPER_BROKER" }] } },
};

test("orders read route checks operator session before fixed no-store ledger reads", async () => {
  const source = readFileSync(new URL("../src/app/api/orders/observations/route.ts", import.meta.url), "utf8");
  const bridge = compile("export const getOrders=()=>globalThis.ordersReads.orders();export const getFills=()=>globalThis.ordersReads.fills();export const sessionStatus=()=>globalThis.ordersReads.sessionStatus();");
  const route = await import(compile(source.replace('from "@/lib/control-api"',`from "${bridge}"`).replace('from "@/features/operator-session/server"',`from "${bridge}"`)));
  let reads = 0;
  globalThis.ordersReads = {
    authorized: false,
    sessionStatus() { return Promise.resolve(new Response(null, {status:this.authorized?200:401})); },
    orders() { reads++; return Promise.resolve(envelope.orders); },
    fills() { reads++; return Promise.resolve(envelope.fills); },
  };
  try {
    assert.equal((await route.GET(new Request("http://local/api/orders/observations?target=https://outside.invalid"))).status, 422);
    assert.equal((await route.GET(new Request("http://local/api/orders/observations"))).status, 401);
    assert.equal(reads, 0);
    globalThis.ordersReads.authorized = true;
    const response = await route.GET(new Request("http://local/api/orders/observations"));
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(Object.keys(await response.json()).sort(), ["observed_at", "orders", "fills"].sort());
    assert.equal(reads, 2);
  } finally { delete globalThis.ordersReads; }
});

test("orders browser reader accepts only bounded paper receipts and fills", async () => {
  const source = readFileSync(new URL("../src/features/orders/orders-live-read.ts", import.meta.url), "utf8");
  const {parseOrdersSnapshot, readLiveOrders} = await import(compile(source));
  assert(parseOrdersSnapshot(envelope));
  assert.equal(parseOrdersSnapshot({...envelope, unexpected:true}), null);
  assert.equal(parseOrdersSnapshot({...envelope, orders:{ok:true,value:{orders:[{...envelope.orders.value.orders[0],broker_route:"LIVE"}]}}}), null);
  assert.equal(parseOrdersSnapshot({...envelope, fills:{ok:true,value:{fills:[{...envelope.fills.value.fills[0], execution_authority:"BROKER"}]}}}), null);
  const calls=[];
  globalThis.fetch=async(url,options)=>{calls.push([url,options]);return new Response(JSON.stringify(envelope));};
  try { const abort=new AbortController(); assert.deepEqual(await readLiveOrders(abort.signal),envelope); assert.equal(calls[0][0],"/api/orders/observations"); assert.equal(calls[0][1].credentials,"same-origin"); assert.equal(calls[0][1].cache,"no-store"); assert.equal(calls[0][1].signal,abort.signal); }
  finally { delete globalThis.fetch; }
});

test("Orders page mounts live receipts and fills alongside reconciliation controls", () => {
  const page=readFileSync(new URL("../src/app/[section]/page.tsx",import.meta.url),"utf8");
  assert.match(page,/<OrdersLivePanel initialReads=\{orderReads\} \/>/);
  assert.doesNotMatch(page,/<PaperReconciliationPanel \/>\s*\{section === "orders" && <section/);
  assert.match(page,/<PaperReconciliationPanel \/>/);
});

test("orders live store reuses one feed subscription and withholds on disconnect and timeout", async () => {
  const path=new URL("../src/features/orders/orders-live-store.ts",import.meta.url);
  assert(existsSync(path),"Orders live store required");
  const {createOrdersLiveStore}=await import(compile(readFileSync(path,"utf8")));
  let now=Date.parse("2026-10-12T00:00:00Z"),sequence=0,id=0,signal={state:"CONNECTING",frame:null};
  const timers=new Map(),subscribers=new Set(),pending=[],reads=[];
  const epoch="12345678-1234-4123-8123-123456789abc";
  const store=createOrdersLiveStore({clock:()=>now,signal:()=>signal,subscribeTrigger(callback){subscribers.add(callback);return()=>subscribers.delete(callback);},read(abort){reads.push(abort);return new Promise(resolve=>pending.push(resolve));},schedule(callback,delay){const key=++id;timers.set(key,{callback,at:now+delay});return key;},cancel(key){timers.delete(key);}});
  const offA=store.subscribe(()=>{}),offB=store.subscribe(()=>{});
  assert.equal(subscribers.size,1);
  signal={state:"CURRENT",frame:{epoch,sequence:++sequence,captured_at:new Date(now).toISOString(),valid_until:new Date(now+3000).toISOString()}};
  for(const callback of subscribers)callback();
  assert.equal(reads.length,1);
  signal={state:"DISCONNECTED",frame:null};
  for(const callback of subscribers)callback();
  assert(reads[0].aborted);
  pending[0](structuredClone(envelope));
  for(let index=0;index<8;index++)await Promise.resolve();
  assert.equal(store.snapshot().snapshot,null);
  offA();offB();assert.equal(timers.size,0);
});

test("orders live store aborts a read after four seconds and clears its retry timer on unsubscribe", async () => {
  const {createOrdersLiveStore}=await import(compile(readFileSync(new URL("../src/features/orders/orders-live-store.ts",import.meta.url),"utf8")));
  let now=Date.parse("2026-10-12T00:00:00Z"),id=0,signal={state:"CONNECTING",frame:null};
  const timers=new Map(),subscribers=new Set(),pending=[],reads=[];
  const store=createOrdersLiveStore({clock:()=>now,signal:()=>signal,subscribeTrigger(callback){subscribers.add(callback);return()=>subscribers.delete(callback);},read(abort){reads.push(abort);return new Promise(resolve=>pending.push(resolve));},schedule(callback,delay){const key=++id;timers.set(key,{callback,at:now+delay});return key;},cancel(key){timers.delete(key);}});
  const unsubscribe=store.subscribe(()=>{});
  signal={state:"CURRENT",frame:{epoch:"12345678-1234-4123-8123-123456789abc",sequence:1,captured_at:new Date(now).toISOString(),valid_until:new Date(now+3000).toISOString()}};
  for(const callback of subscribers)callback();
  assert.equal(reads.length,1);
  now+=4000;
  for(const [key,timer] of [...timers])if(timer.at<=now){timers.delete(key);timer.callback();}
  assert(reads[0].aborted);
  assert.equal(store.snapshot().snapshot,null);
  pending[0](structuredClone(envelope));
  for(let index=0;index<8;index++)await Promise.resolve();
  assert.equal(store.snapshot().snapshot,null);
  unsubscribe();
  assert.equal(timers.size,0);
});

"use client";

import { useSyncExternalStore } from "react";
import { readRealtimeView, subscribeRealtime } from "@/features/realtime/use-realtime";
import { readLiveOrders } from "./orders-live-read";
import { createOrdersLiveStore } from "./orders-live-store";
import type { OrdersReads } from "./orders-live-read";

const store = createOrdersLiveStore({ clock: Date.now, signal: readRealtimeView, subscribeTrigger: subscribeRealtime, read: readLiveOrders, schedule: (callback, delay) => window.setTimeout(callback, delay), cancel: id => window.clearTimeout(id) });

function OrderTable({ result }: { result: OrdersReads[0] }) {
  return <section className="panel content-panel"><div className="panel-heading"><h2>Paper order receipts</h2><span>{result.ok ? `${result.value.orders.length} RECORDS` : "UNAVAILABLE"}</span></div>{result.ok && result.value.orders.length ? <div className="table-scroll"><table><caption className="sr-only">PAPER order receipts and statuses</caption><thead><tr><th scope="col">Order ID</th><th scope="col">Instrument</th><th scope="col">Intent ID</th><th scope="col">Risk ID</th><th scope="col">Route</th><th scope="col">Broker ID</th><th scope="col">Status</th></tr></thead><tbody>{result.value.orders.map(order => <tr key={order.order_id}><td className="mono">{order.order_id}</td><td>{order.instrument_id}</td><td>{order.intent_id}</td><td className="mono">{order.risk_id}</td><td>{order.broker_route}</td><td className="mono">{order.broker_order_id ?? "—"}</td><td>{order.status}</td></tr>)}</tbody></table></div> : <div className="empty-list">{result.ok ? "No paper orders recorded." : "Control API unavailable."}</div>}</section>;
}

function FillTable({ result }: { result: OrdersReads[1] }) {
  return <section className="panel content-panel"><div className="panel-heading"><h2>Simulated fills</h2><span>{result.ok ? `${result.value.fills.length} RECORDS` : "UNAVAILABLE"}</span></div>{result.ok && result.value.fills.length ? <div className="table-scroll"><table><caption className="sr-only">PAPER simulated fill records</caption><thead><tr><th scope="col">Fill ID</th><th scope="col">Order ID</th><th scope="col">Instrument</th><th scope="col">Side</th><th scope="col">Quantity</th><th scope="col">Price</th><th scope="col">Fee</th><th scope="col">Source</th></tr></thead><tbody>{result.value.fills.map(fill => <tr key={fill.fill_id}><td className="mono">{fill.fill_id}</td><td className="mono">{fill.order_id}</td><td>{fill.instrument_id}</td><td>{fill.side}</td><td>{fill.quantity}</td><td>{fill.price}</td><td>{fill.fee}</td><td>{fill.source === "PAPER_BROKER" ? "Simulator" : "Manual"}</td></tr>)}</tbody></table></div> : <div className="empty-list">{result.ok ? "No simulated fills recorded." : "Fill service unavailable."}</div>}</section>;
}

export function OrdersLivePanel({ initialReads }: { initialReads: OrdersReads }) {
  const view = useSyncExternalStore(store.subscribe, store.snapshot, store.serverSnapshot);
  const snapshot = view.state === "CURRENT" ? view.snapshot : null;
  const unavailable = { ok: false as const, reason: "UNAVAILABLE" };
  const reads: OrdersReads = snapshot ? [snapshot.orders, snapshot.fills] : view.state === "SERVER" ? initialReads : [unavailable, unavailable];
  const automatic = view.state === "CURRENT";
  return <>
    {view.state !== "SERVER" && <p className="panel-muted" role="status">{automatic ? "Protected paper order and fill observations refresh every 15 seconds while the authenticated paper stream is current. Receipts and fills are separate reads." : "Protected paper order and fill observations are withheld while the authenticated stream or read is unavailable."}</p>}
    <OrderTable result={reads[0]} />
    <FillTable result={reads[1]} />
  </>;
}

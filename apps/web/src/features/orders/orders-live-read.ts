export type PaperOrder = { order_id: string; intent_id: string; risk_id: string; instrument_id: string; status: string; broker_route: "PAPER"; broker_order_id?: string };
export type PaperFill = { fill_id: string; order_id: string; instrument_id: string; side: "BUY" | "SELL"; quantity: string; price: string; fee: string; observed_at: string; source: "PAPER_MANUAL" | "PAPER_BROKER" };
export type OrdersRead<T> = { ok: true; value: T } | { ok: false; reason: string };
export type OrdersReads = [OrdersRead<{ orders: PaperOrder[] }>, OrdersRead<{ fills: PaperFill[] }>];
export type OrdersSnapshot = { observed_at: string; orders: OrdersReads[0]; fills: OrdersReads[1] };

const MAX_BYTES = 262144;
const MAX_ROWS = 1024;
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.prototype.hasOwnProperty.call(value, key));

function parseRead<T>(value: unknown, parse: (value: unknown) => T | null): OrdersRead<T> | null {
  if (!record(value)) return null;
  if (value.ok === false && exact(value, ["ok", "reason"]) && typeof value.reason === "string" && value.reason.length <= 80) return { ok: false, reason: value.reason };
  if (value.ok === true && exact(value, ["ok", "value"])) {
    const parsed = parse(value.value);
    return parsed === null ? null : { ok: true, value: parsed };
  }
  return null;
}

function parseOrders(value: unknown): { orders: PaperOrder[] } | null {
  if (!record(value) || !exact(value, ["orders"]) || !Array.isArray(value.orders) || value.orders.length > MAX_ROWS) return null;
  const orders: PaperOrder[] = [];
  for (const item of value.orders) {
    if (!record(item) || (Object.keys(item).length !== 6 && Object.keys(item).length !== 7) || !["order_id", "intent_id", "risk_id", "instrument_id", "status", "broker_route"].every(key => Object.prototype.hasOwnProperty.call(item, key)) || Object.keys(item).some(key => !["order_id", "intent_id", "risk_id", "instrument_id", "status", "broker_route", "broker_order_id"].includes(key)) || ![item.order_id, item.intent_id, item.risk_id, item.instrument_id, item.status].every(field => typeof field === "string" && field.length <= 256) || item.broker_route !== "PAPER" || (item.broker_order_id !== undefined && (typeof item.broker_order_id !== "string" || item.broker_order_id.length > 256))) return null;
    orders.push({ order_id: item.order_id as string, intent_id: item.intent_id as string, risk_id: item.risk_id as string, instrument_id: item.instrument_id as string, status: item.status as string, broker_route: "PAPER", ...(typeof item.broker_order_id === "string" ? { broker_order_id: item.broker_order_id } : {}) });
  }
  return { orders };
}

function parseFills(value: unknown): { fills: PaperFill[] } | null {
  if (!record(value) || !exact(value, ["fills"]) || !Array.isArray(value.fills) || value.fills.length > MAX_ROWS) return null;
  const fills: PaperFill[] = [];
  for (const item of value.fills) {
    if (!record(item) || !exact(item, ["fill_id", "order_id", "instrument_id", "side", "quantity", "price", "fee", "observed_at", "source"]) || ![item.fill_id, item.order_id, item.instrument_id, item.quantity, item.price, item.fee, item.observed_at].every(field => typeof field === "string" && field.length <= 256) || (item.side !== "BUY" && item.side !== "SELL") || (item.source !== "PAPER_MANUAL" && item.source !== "PAPER_BROKER")) return null;
    fills.push(item as PaperFill);
  }
  return { fills };
}

export function parseOrdersSnapshot(value: unknown): OrdersSnapshot | null {
  if (!record(value) || !exact(value, ["observed_at", "orders", "fills"]) || typeof value.observed_at !== "string") return null;
  const time = Date.parse(value.observed_at);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value.observed_at) || !Number.isFinite(time) || new Date(time).toISOString() !== value.observed_at) return null;
  const orders = parseRead(value.orders, parseOrders);
  const fills = parseRead(value.fills, parseFills);
  return orders && fills ? { observed_at: value.observed_at, orders, fills } : null;
}

export async function readLiveOrders(signal: AbortSignal): Promise<OrdersSnapshot | null> {
  const response = await fetch("/api/orders/observations", { cache: "no-store", redirect: "error", credentials: "same-origin", signal });
  if (!response.ok) { await response.body?.cancel(); return null; }
  const reader = response.body?.getReader();
  if (!reader) return null;
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_BYTES) throw new Error("Orders read exceeded capacity");
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return parseOrdersSnapshot(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
}

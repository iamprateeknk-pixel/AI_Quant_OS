type Fields = Record<string, unknown>;
function fields(value: unknown): Fields {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Object required");
  return value as Fields;
}
function text(value: unknown, pattern: RegExp): string {
  if (typeof value !== "string" || !pattern.test(value)) throw new Error("Invalid field");
  return value;
}
function choice(value: unknown, choices: string[]): string {
  if (typeof value !== "string" || !choices.includes(value)) throw new Error("Invalid choice");
  return value;
}
function positiveId(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) throw new Error("Unsafe identity");
  return value;
}
function denied(value: Fields) {
  if (value.may_submit_orders !== false || value.may_affect_portfolio !== false) throw new Error("Authority denied");
}
function unverified(value: Fields) {
  denied(value);
  if (value.native_attestation !== "UNVERIFIED") throw new Error("Unverified source required");
}
function micros(value: unknown): bigint {
  const stamp = text(value, /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/);
  const parts = stamp.match(/^(.*?)(?:\.(\d{1,6}))?(?:Z|\+00:00)$/)!;
  const seconds = Date.parse(parts[1] + "Z");
  if (!Number.isFinite(seconds) || new Date(seconds).toISOString().slice(0, 19) !== parts[1] || Number(stamp.slice(0, 4)) < 1) throw new Error("UTC required");
  return BigInt(seconds) * BigInt(1000) + BigInt((parts[2] ?? "").padEnd(6, "0"));
}
function stamp(value: unknown): string { micros(value); return value as string; }
function decimal(value: unknown, sign: "any" | "positive" | "nonnegative" = "any"): string {
  const amount = text(value, /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?$/);
  const number = Number(amount);
  if (amount.length > 80 || !Number.isFinite(number) || (number === 0 && /[1-9]/.test(amount.split(/[eE]/)[0])) || (sign === "positive" && number <= 0) || (sign === "nonnegative" && number < 0)) throw new Error("Invalid decimal");
  return amount;
}
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const hash = /^[a-f0-9]{64}$/;
const symbol = /^[A-Za-z0-9_.-]{1,64}$/;
function account(value: unknown) {
  const v = fields(value);
  return {currency:text(v.currency, /^[A-Z]{3}$/), balance:decimal(v.balance), credit:decimal(v.credit,"nonnegative"), equity:decimal(v.equity), profit:decimal(v.profit), margin:decimal(v.margin,"nonnegative"), margin_free:decimal(v.margin_free), margin_mode:choice(v.margin_mode,["NETTING","EXCHANGE","HEDGING"]), leverage:positiveId(v.leverage)};
}
function position(value: unknown) {
  const v = fields(value);
  if (v.quantity_basis !== "CURRENT_SYMBOL_METADATA") throw new Error("Invalid units basis");
  return {ticket:positiveId(v.ticket), identifier:positiveId(v.identifier), instrument_id:text(v.instrument_id,symbol), venue_symbol:text(v.venue_symbol,symbol), side:choice(v.side,["BUY","SELL"]), volume_lots:decimal(v.volume_lots,"positive"), quantity_units:decimal(v.quantity_units,"positive"), price_open:decimal(v.price_open,"positive"), price_current:decimal(v.price_current,"positive"), profit:decimal(v.profit), swap:decimal(v.swap), currency:text(v.currency,/^[A-Z]{3}$/), created_at:stamp(v.created_at), updated_at:stamp(v.updated_at)};
}
function order(value: unknown) {
  const v = fields(value);
  return {ticket:positiveId(v.ticket), instrument_id:text(v.instrument_id,symbol), venue_symbol:text(v.venue_symbol,symbol), kind:choice(v.kind,["BUY","SELL","BUY_LIMIT","SELL_LIMIT","BUY_STOP","SELL_STOP","BUY_STOP_LIMIT","SELL_STOP_LIMIT"]), state:choice(v.state,["STARTED","PLACED","PARTIAL","REQUEST_ADD","REQUEST_MODIFY","REQUEST_CANCEL"]), initial_lots:decimal(v.initial_lots,"positive"), remaining_lots:decimal(v.remaining_lots,"nonnegative"), price_open:decimal(v.price_open,"nonnegative"), stop_limit_price:decimal(v.stop_limit_price,"nonnegative"), created_at:stamp(v.created_at)};
}
function rows<T>(value: unknown, parse: (v: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > 8) throw new Error("Bounded rows required");
  return value.map(parse);
}
function record(value: unknown) {
  const v = fields(value); unverified(v);
  if (v.broker_id !== "EXNESS_MT5_DEMO" || v.mode !== "DEMO_OBSERVATION" || v.quantity_basis !== "CURRENT_SYMBOL_METADATA" || v.history_attestation !== "UNVERIFIED" || v.account_freshness !== "UNVERIFIED" || v.consistency !== "NON_ATOMIC_SEQUENTIAL_READ") throw new Error("Unverified demo observation required");
  const observed_at = stamp(v.observed_at), valid_until = stamp(v.valid_until), history_start = stamp(v.history_start), history_through = stamp(v.history_through);
  const lease = micros(valid_until) - micros(observed_at);
  if (lease <= BigInt(0) || lease > BigInt(3000000) || micros(history_start) >= micros(history_through) || micros(history_through) > micros(observed_at)) throw new Error("Invalid source interval");
  const money = account(v.account), positions = rows(v.positions,position), orders = rows(v.orders,order);
  if (positions.some(p=>p.currency !== money.currency) || new Set(positions.map(p=>p.ticket)).size !== positions.length || new Set(positions.map(p=>p.identifier)).size !== positions.length || new Set(orders.map(o=>o.ticket)).size !== orders.length) throw new Error("Conflicting identities or currency");
  return {source_attempt_id:text(v.source_attempt_id,uuid), source_receipt_ref:text(v.source_receipt_ref,hash), account_scope_ref:text(v.account_scope_ref,hash), observed_at,valid_until,history_start,history_through,account:money,positions,orders};
}
export type DemoPortfolioRead = {configured:boolean; state:"AVAILABLE"|"STALE"|"UNAVAILABLE"; reason:string|null; run_id:string|null; published_at:string|null; record:ReturnType<typeof record>|null};
export function parseDemoPortfolio(value: unknown, observedAt: string): DemoPortfolioRead | null {
  try {
    const now = micros(observedAt), v = fields(value); denied(v);
    if (v.schema_version !== "1.0" || v.source_kind !== "LOCAL_UNVERIFIED_DEMO_PORTFOLIO" || typeof v.configured !== "boolean") return null;
    const observation = fields(v.observation); denied(observation);
    const state = choice(observation.state,["AVAILABLE","STALE","UNAVAILABLE"]);
    const empty:DemoPortfolioRead = {configured:v.configured,state:"UNAVAILABLE",reason:null,run_id:null,published_at:null,record:null};
    if (observation.publication === null) return state === "UNAVAILABLE" ? empty : null;
    if (!v.configured) return null;
    const publication = fields(observation.publication); unverified(publication);
    if (publication.schema_version !== "1.0" || publication.observation_kind !== "LOCAL_UNVERIFIED_DEMO_PORTFOLIO") return null;
    const run_id = text(publication.run_id,uuid), published_at = stamp(publication.published_at);
    if (micros(published_at) > now) return null;
    if (publication.state === "EMPTY" || publication.state === "WITHHELD") {
      if (state !== "UNAVAILABLE" || publication.record !== null || (publication.state === "EMPTY" ? publication.code !== null : !["SOURCE_UNAVAILABLE","CLOCK"].includes(String(publication.code)))) return null;
      return {...empty,run_id,published_at,reason:publication.code === null ? "EMPTY" : String(publication.code)};
    }
    if (publication.state !== "OBSERVED" || publication.code !== null || state === "UNAVAILABLE") return null;
    const saved = record(publication.record);
    if (micros(saved.observed_at) > micros(published_at)) return null;
    return {configured:true,state:state === "STALE" || now >= micros(saved.valid_until) ? "STALE" : "AVAILABLE",reason:null,run_id,published_at,record:saved};
  } catch { return null; }
}

import type { OrdersSnapshot } from "./orders-live-read";

type Signal = { state: string; frame: { epoch: string; sequence: number; captured_at: string; valid_until: string } | null };
export type OrdersLiveView = { state: "SERVER" | "WAITING" | "CURRENT" | "WITHHELD"; snapshot: OrdersSnapshot | null };
type Options = { clock: () => number; signal: () => Signal; subscribeTrigger: (callback: () => void) => () => void; read: (signal: AbortSignal) => Promise<OrdersSnapshot | null>; schedule: (callback: () => void, delay: number) => number; cancel: (id: number) => void };
const INITIAL: OrdersLiveView = Object.freeze({ state: "SERVER", snapshot: null });
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;

function currentFrame(signal: Signal, now: number) {
  const frame = signal.frame;
  return signal.state === "CURRENT" && frame && UUID.test(frame.epoch) && Number.isSafeInteger(frame.sequence) && frame.sequence > 0 && Date.parse(frame.captured_at) <= now && now < Date.parse(frame.valid_until) ? frame : null;
}

/** Refresh fixed paper receipts only while the shared authenticated feed is current. */
export function createOrdersLiveStore(options: Options) {
  let view = INITIAL, detach: (() => void) | null = null, epoch: string | null = null, sequence = 0, generation = 0, nextRead = 0;
  let pending: AbortController | null = null, deadline: number | null = null, poll: number | null = null, expiry: number | null = null;
  const listeners = new Set<() => void>();
  const publish = (next: OrdersLiveView) => { view = next; for (const listener of [...listeners]) listener(); };
  const cancel = (id: number | null) => { if (id !== null) options.cancel(id); };
  const withhold = () => { cancel(expiry); expiry = null; publish({ state: "WITHHELD", snapshot: null }); };
  const stopRead = () => { generation++; pending?.abort(); pending = null; cancel(deadline); deadline = null; };
  const schedulePoll = () => { cancel(poll); poll = options.schedule(() => { poll = null; if (listeners.size) request(); }, Math.max(0, nextRead - options.clock())); };
  const request = () => {
    const frame = currentFrame(options.signal(), options.clock());
    if (!frame || frame.epoch !== epoch || frame.sequence < sequence) { stopRead(); withhold(); return; }
    if (pending || !listeners.size) return;
    if (options.clock() < nextRead) { schedulePoll(); return; }
    nextRead = options.clock() + 15000;
    const owner = new AbortController(), identity = ++generation;
    pending = owner;
    deadline = options.schedule(() => { if (pending === owner) { stopRead(); withhold(); schedulePoll(); } }, 4000);
    void options.read(owner.signal).then(snapshot => {
      if (identity !== generation || pending !== owner || !listeners.size) return;
      const active = currentFrame(options.signal(), options.clock());
      if (!active || active.epoch !== epoch) { stopRead(); withhold(); return; }
      cancel(deadline); deadline = null; pending = null;
      const now = options.clock(), captured = snapshot ? Date.parse(snapshot.observed_at) : NaN;
      if (!snapshot || !Number.isFinite(captured) || captured > now || now - captured >= 45000) withhold();
      else { cancel(expiry); publish({ state: "CURRENT", snapshot }); expiry = options.schedule(() => { expiry = null; withhold(); }, captured + 45000 - now); }
      schedulePoll();
    }).catch(() => { if (identity !== generation || pending !== owner) return; stopRead(); withhold(); schedulePoll(); });
  };
  const receive = () => {
    const frame = currentFrame(options.signal(), options.clock());
    if (!frame) { stopRead(); cancel(poll); poll = null; withhold(); return; }
    if (epoch !== frame.epoch) { stopRead(); withhold(); epoch = frame.epoch; sequence = 0; nextRead = 0; }
    if (frame.sequence <= sequence) { stopRead(); withhold(); return; }
    sequence = frame.sequence;
    request();
  };
  return {
    snapshot: () => view,
    serverSnapshot: () => INITIAL,
    subscribe(listener: () => void) {
      listeners.add(listener);
      if (listeners.size === 1) { publish({ state: "WAITING", snapshot: null }); detach = options.subscribeTrigger(receive); receive(); }
      return () => {
        listeners.delete(listener);
        if (!listeners.size) { detach?.(); detach = null; stopRead(); cancel(expiry); cancel(poll); expiry = null; poll = null; epoch = null; sequence = 0; nextRead = 0; view = { state: "WITHHELD", snapshot: null }; }
      };
    },
  };
}

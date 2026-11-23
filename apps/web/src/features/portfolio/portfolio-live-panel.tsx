"use client";

import { useSyncExternalStore } from "react";
import { readRealtimeView, subscribeRealtime } from "@/features/realtime/use-realtime";
import { CurrencyExposurePanel } from "./currency-exposure-panel";
import { InstrumentPositionsPanel } from "./instrument-positions-panel";
import { readLivePortfolio } from "./portfolio-live-read";
import { createPortfolioLiveStore } from "./portfolio-live-store";
import type { PortfolioReads } from "./portfolio-live-read";

const store = createPortfolioLiveStore({
  clock: Date.now,
  signal: readRealtimeView,
  subscribeTrigger: subscribeRealtime,
  read: readLivePortfolio,
  schedule: (callback, delay) => window.setTimeout(callback, delay),
  cancel: (id) => window.clearTimeout(id),
});

export function PortfolioLivePanel({ initialReads }: { initialReads: PortfolioReads }) {
  const view = useSyncExternalStore(store.subscribe, store.snapshot, store.serverSnapshot);
  const snapshot = view.state === "CURRENT" ? view.snapshot : null;
  const unavailable = { ok: false as const, reason: "UNAVAILABLE" };
  const reads: PortfolioReads = snapshot
    ? [snapshot.positions, snapshot.currencyPortfolio]
    : view.state === "SERVER" ? initialReads : [unavailable, unavailable];
  const automatic = view.state === "CURRENT";

  return <>
    {view.state !== "SERVER" && <p className="panel-muted" role="status">{automatic
      ? "Protected portfolio observations refresh every 15 seconds while the authenticated paper stream is current. Position and currency ledgers are separate reads, not an atomic snapshot."
      : "Protected portfolio observations are withheld while the authenticated stream or read is unavailable. No position or exposure is inferred."}</p>}
    <InstrumentPositionsPanel result={reads[0]} />
    <CurrencyExposurePanel result={reads[1]} />
  </>;
}

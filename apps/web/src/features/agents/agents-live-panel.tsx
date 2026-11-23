"use client";

import { useSyncExternalStore } from "react";
import { readRealtimeView, subscribeRealtime } from "@/features/realtime/use-realtime";
import { AgentRegistryPanel } from "./registry-panel";
import { readLiveAgents, type AgentsRead } from "./agents-live-read";
import { createAgentsLiveStore } from "./agents-live-store";

const store = createAgentsLiveStore({ clock: Date.now, signal: readRealtimeView, subscribeTrigger: subscribeRealtime, read: readLiveAgents, schedule: (callback, delay) => window.setTimeout(callback, delay), cancel: id => window.clearTimeout(id) });

export function AgentsLivePanel({ initialRead }: { initialRead: AgentsRead }) {
  const view = useSyncExternalStore(store.subscribe, store.snapshot, store.serverSnapshot);
  const snapshot = view.state === "CURRENT" ? view.snapshot : null;
  const result: AgentsRead = snapshot ? snapshot.registry : view.state === "SERVER" ? initialRead : { ok: false, reason: "UNAVAILABLE" };
  const automatic = view.state === "CURRENT";
  return <>
    {view.state !== "SERVER" && <p className="panel-muted" role="status">{automatic ? "The protected public agent registry refreshes every 15 seconds while the authenticated paper stream is current. Development status and declared permissions do not imply runtime readiness or capital authority." : "The protected agent registry is withheld while the authenticated stream or read is unavailable."}</p>}
    <AgentRegistryPanel result={result} />
  </>;
}

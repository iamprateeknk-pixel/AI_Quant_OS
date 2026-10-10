"use client";

import {useSyncExternalStore} from "react";
import {createRealtimeStore} from "./store";

const store=createRealtimeStore({
  connect:()=>new EventSource("/api/realtime/events"),
  clock:Date.now,
  schedule:(callback,delay)=>window.setTimeout(callback,delay),
  cancel:id=>window.clearTimeout(id),
});

export function useRealtime() {
  return useSyncExternalStore(store.subscribe,store.snapshot,store.serverSnapshot);
}

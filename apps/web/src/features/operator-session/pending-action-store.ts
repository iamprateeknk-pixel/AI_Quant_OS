"use client";

import { useSyncExternalStore } from "react";
import { clearPending, decodePending, PENDING_KEY, savePending, type Command } from "./pending-action";

const EVENT = "ai-quant-pending-control-changed";
function subscribe(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener(EVENT, callback);
  return () => { window.removeEventListener("storage", callback); window.removeEventListener(EVENT, callback); };
}
function snapshot(): string | null {
  try { return window.sessionStorage.getItem(PENDING_KEY); } catch { return "STORAGE_UNAVAILABLE"; }
}
const serverSnapshot = () => null;

export function usePendingAction(): { command: Command | null; issue: string | null } {
  const raw = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  if (raw === null) return { command: null, issue: null };
  try { return { command: decodePending(raw), issue: null }; }
  catch { return { command: null, issue: "Browser action checkpoint unavailable or invalid. Controls are disabled; retain the original record for recovery." }; }
}
export function retainAction(command: Command) {
  savePending(window.sessionStorage, command);
  window.dispatchEvent(new Event(EVENT));
}
export function resolveAction(actionId: string) {
  clearPending(window.sessionStorage, actionId);
  window.dispatchEvent(new Event(EVENT));
}

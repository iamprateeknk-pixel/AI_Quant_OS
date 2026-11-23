import { parseRegistry, type AgentManifest } from "./registry";

export type AgentsRead = { ok: true; value: AgentManifest[] } | { ok: false; reason: string };
export type AgentsSnapshot = { observed_at: string; registry: AgentsRead };
const MAX_BYTES = 131072;
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.prototype.hasOwnProperty.call(value, key));

export function parseAgentsSnapshot(value: unknown): AgentsSnapshot | null {
  if (!record(value) || !exact(value, ["observed_at", "registry"]) || typeof value.observed_at !== "string") return null;
  const time = Date.parse(value.observed_at);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value.observed_at) || !Number.isFinite(time) || new Date(time).toISOString() !== value.observed_at || !record(value.registry)) return null;
  if (value.registry.ok === false && exact(value.registry, ["ok", "reason"]) && typeof value.registry.reason === "string" && value.registry.reason.length <= 80) return { observed_at: value.observed_at, registry: { ok: false, reason: value.registry.reason } };
  if (value.registry.ok !== true || !exact(value.registry, ["ok", "value"])) return null;
  const agents = parseRegistry({ schema_version: "1.0", mode: "PAPER", agents: value.registry.value });
  return agents === null ? null : { observed_at: value.observed_at, registry: { ok: true, value: agents } };
}

export async function readLiveAgents(signal: AbortSignal): Promise<AgentsSnapshot | null> {
  const response = await fetch("/api/agents/observations", { cache: "no-store", redirect: "error", credentials: "same-origin", signal });
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
      if (length > MAX_BYTES) throw new Error("Agent read exceeded capacity");
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return parseAgentsSnapshot(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
}

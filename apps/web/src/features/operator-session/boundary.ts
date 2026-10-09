/** Pure validation at the browser/server credential boundary. */
export function requireOrigin(request: Request, configured: string): void {
  const origin = new URL(configured);
  if (origin.origin !== configured || !["http:", "https:"].includes(origin.protocol) ||
      request.headers.get("origin") !== configured || request.headers.get("host") !== origin.host) {
    throw new Error("Untrusted request origin");
  }
}

export function backendOrigin(configured: string): string {
  const url = new URL(configured);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash) throw new Error("Local API origin required");
  return url.origin;
}

export async function readJson(request: Request, maximum = 4096): Promise<unknown> {
  if (!Number.isSafeInteger(maximum) || maximum < 1 || maximum > 131072) throw new Error("Invalid body bound");
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Request body required");
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) throw new Error("Request body too large");
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

export function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Object required");
  return value as Record<string, unknown>;
}

export function loginCredential(value: unknown): string {
  const input = record(value);
  if (Object.keys(input).length !== 1 || typeof input.credential !== "string" ||
      !input.credential || input.credential.length > 512 || /[\r\n]/.test(input.credential)) {
    throw new Error("Invalid access code");
  }
  return input.credential;
}

export type PublicSession = { mode: "PAPER"; session_id: string; created_at: string; expires_at: string };
export function publicSession(value: unknown): PublicSession {
  const input = record(value);
  if (input.mode !== "PAPER" || typeof input.session_id !== "string" ||
      !/^[0-9a-f-]{36}$/.test(input.session_id) || typeof input.created_at !== "string" ||
      typeof input.expires_at !== "string" || !Number.isFinite(Date.parse(input.created_at)) ||
      Date.parse(input.expires_at) - Date.parse(input.created_at) !== 900000) {
    throw new Error("Invalid backend session");
  }
  return { mode: "PAPER", session_id: input.session_id, created_at: input.created_at, expires_at: input.expires_at };
}


const sessionIdentity = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(v);
function utcStamp(v: unknown): v is string {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v) && Number(v.slice(0,4)) >= 1 && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0,19) === v.slice(0,19);
}
export type SessionInventory = {
  schema_version: "1.0"; mode: "PAPER"; scope: "LOCAL_SINGLE_OWNER"; observed_at: string;
  current_session_id: string; source_head: string; source_event_count: number;
  sessions: (PublicSession & {status: "CURRENT" | "ACTIVE" | "EXPIRED" | "FUTURE"})[];
};
export function publicSessionInventory(value: unknown): SessionInventory {
  const input = record(value);
  if (input.schema_version !== "1.0" || input.mode !== "PAPER" || input.scope !== "LOCAL_SINGLE_OWNER" || !utcStamp(input.observed_at) || !sessionIdentity(input.current_session_id) || typeof input.source_head !== "string" || !/^[a-f0-9]{64}$/.test(input.source_head) || input.source_head === "0".repeat(64) || !Number.isSafeInteger(input.source_event_count) || !Array.isArray(input.sessions) || input.sessions.length < 1 || input.sessions.length > 128 || (input.source_event_count as number) < input.sessions.length || (input.source_event_count as number) > 8192) throw new Error("Invalid session inventory");
  const seen = new Set<string>(), now = Date.parse(input.observed_at);
  let current = 0;
  const sessions = input.sessions.map((value): SessionInventory["sessions"][number] => {
    const row = record(value), base = publicSession(row);
    if (!sessionIdentity(row.session_id) || !utcStamp(row.created_at) || !utcStamp(row.expires_at) || seen.has(base.session_id)) throw new Error("Invalid session identity");
    seen.add(base.session_id);
    const start = Date.parse(base.created_at), end = Date.parse(base.expires_at);
    const status = base.session_id === input.current_session_id ? "CURRENT" : now < start ? "FUTURE" : now >= end ? "EXPIRED" : "ACTIVE";
    if (row.status !== status || (status === "CURRENT" && !(start <= now && now < end))) throw new Error("Invalid session state");
    if (status === "CURRENT") current++;
    return {...base, status};
  });
  if (current !== 1) throw new Error("Current session missing");
  return {schema_version:"1.0",mode:"PAPER",scope:"LOCAL_SINGLE_OWNER",observed_at:input.observed_at,current_session_id:input.current_session_id,source_head:input.source_head,source_event_count:input.source_event_count as number,sessions};
}
export type SessionRevocationReceipt = {schema_version:"1.0";mode:"PAPER";status:"REVOKED";target_session_id:string;actor_session_id:string;current_session_revoked:boolean;revoked_at:string;source_head:string};
export function publicSessionRevocation(value: unknown,target:string,actor:string): SessionRevocationReceipt {
  const v = record(value);
  if(v.schema_version!=="1.0" || v.mode!=="PAPER" || v.status!=="REVOKED" || !sessionIdentity(v.target_session_id) || !sessionIdentity(v.actor_session_id) || v.target_session_id!==target || v.actor_session_id!==actor || v.current_session_revoked!==(target===actor) || !utcStamp(v.revoked_at) || typeof v.source_head!=="string" || !/^[a-f0-9]{64}$/.test(v.source_head)) throw new Error("Invalid session revocation receipt");
  return {schema_version:"1.0",mode:"PAPER",status:"REVOKED",target_session_id:v.target_session_id,actor_session_id:v.actor_session_id,current_session_revoked:v.current_session_revoked,revoked_at:v.revoked_at,source_head:v.source_head};
}

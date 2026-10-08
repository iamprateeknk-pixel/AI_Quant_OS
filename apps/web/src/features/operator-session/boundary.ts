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

export async function readJson(request: Request): Promise<unknown> {
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Request body required");
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4096) throw new Error("Request body too large");
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

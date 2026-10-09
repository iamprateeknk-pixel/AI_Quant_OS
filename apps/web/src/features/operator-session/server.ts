import "server-only";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { backendOrigin, loginCredential, publicSession, readJson, record, requireOrigin } from "./boundary";

const COOKIE = "ai_quant_operator";
const origin = () => process.env.AI_QUANT_WEB_ORIGIN ?? "http://127.0.0.1:3000";
const json = (value: unknown, status = 200) => NextResponse.json(value, { status, headers: { "Cache-Control": "no-store" } });

async function upstream(path: string, method: string, headers: Record<string, string>, body?: unknown, maximum = 4096) {
  const base = backendOrigin(process.env.AI_QUANT_API_URL ?? "http://127.0.0.1:8000");
  const response = await fetch(base + path, { method, headers: { ...headers, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), redirect: "error", cache: "no-store", signal: AbortSignal.timeout(3000) });
  if (!response.ok) return { status: response.status, value: null };
  const value = await readJson(new Request("http://local", { method: "POST", body: response.body, duplex: "half" } as RequestInit), maximum);
  return { status: response.status, value };
}

function failure(status: number) {
  const detail = status === 401 ? "Session or access code invalid. Sign in again if your session expired." :
    status === 409 ? "Safety state changed or action identity conflicted. Refresh the state before preparing another action." :
    status === 404 ? "No committed receipt found yet. The action may still be pending." :
    status === 422 ? "Command was not admitted. Check the confirmation and current state." : "Operator service unavailable. Outcome may be unknown.";
  return json({ detail }, [401, 403, 404, 409, 422, 503].includes(status) ? status : 503);
}

function cookieOptions(maxAge: number) {
  return { httpOnly: true, sameSite: "strict" as const, secure: new URL(origin()).protocol === "https:", path: "/", maxAge };
}

export async function sessionStatus() {
  try {
    const token = (await cookies()).get(COOKIE)?.value;
    if (!token) return failure(401);
    const response = await upstream("/api/v1/operator/session", "GET", { "X-Operator-Session": token });
    return response.value ? json(publicSession(response.value)) : failure(response.status);
  } catch { return failure(503); }
}

export async function sessionLogin(request: Request) {
  try { requireOrigin(request, origin()); } catch { return failure(403); }
  let credential: string;
  try { credential = loginCredential(await readJson(request)); } catch { return json({ detail: "A valid access code is required." }, 422); }
  try {
    const response = await upstream("/api/v1/operator/sessions", "POST", { "X-Operator-Token": credential });
    if (!response.value) return failure(response.status);
    const input = record(response.value), session = publicSession(input);
    if (typeof input.session_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(input.session_token)) return failure(503);
    const result = json(session, 201);
    result.cookies.set(COOKIE, input.session_token, cookieOptions(900));
    return result;
  } catch { return failure(503); }
}

export async function sessionLogout(request: Request) {
  try { requireOrigin(request, origin()); } catch { return failure(403); }
  try {
    const token = (await cookies()).get(COOKIE)?.value;
    if (token) {
      const response = await upstream("/api/v1/operator/session", "DELETE", { "X-Operator-Session": token });
      if (response.status !== 200 && response.status !== 401) return failure(response.status);
    }
    const result = json({ status: "SIGNED_OUT" });
    result.cookies.set(COOKIE, "", cookieOptions(0));
    return result;
  } catch { return failure(503); }
}

export async function submitControl(request: Request) {
  try { requireOrigin(request, origin()); } catch { return failure(403); }
  let input: Record<string, unknown>, credential: string;
  try {
    input = record(await readJson(request));
    credential = loginCredential({ credential: input.credential });
    const allowed = ["credential", "action_id", "requested_action", "reason", "operator_session_id", "client_timestamp", "expected_current_state", "confirmation"];
    if (Object.keys(input).some((key) => !allowed.includes(key))) throw new Error("Invalid command");
  } catch { return json({ detail: "A valid command and access code are required." }, 422); }
  const command = { ...input };
  delete command.credential;
  try {
    const token = (await cookies()).get(COOKIE)?.value;
    if (!token) return failure(401);
    const response = await upstream("/api/v1/operator/controls", "POST", { "X-Operator-Session": token, "X-Operator-Reauth": credential }, command);
    return response.value ? json(response.value) : failure(response.status);
  } catch { return failure(503); }
}

export async function controlReceipt(actionId: string) {
  if (!/^[0-9a-f-]{36}$/.test(actionId)) return failure(404);
  try {
    const token = (await cookies()).get(COOKIE)?.value;
    if (!token) return failure(401);
    const response = await upstream("/api/v1/operator/controls/" + actionId, "GET", { "X-Operator-Session": token });
    return response.value ? json(response.value) : failure(response.status);
  } catch { return failure(503); }
}

export type ProtectedRead = { ok: true; value: unknown } | { ok: false; reason: "SIGN_IN" | "CHANGED" | "UNAVAILABLE" | "INVALID_PAGE" };

export async function readHealth(resource: "status" | "incidents", offset = 0, head?: string): Promise<ProtectedRead> {
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 1000 || (head !== undefined && !/^[a-f0-9]{64}$/.test(head)) || (offset > 0 && head === undefined)) return { ok: false, reason: "INVALID_PAGE" };
  const path = resource === "status" ? "/api/v1/paper/health" :
    `/api/v1/paper/health/incidents/page?limit=25&offset=${offset}${head ? `&head=${head}` : ""}`;
  return protectedRead(path);
}

async function protectedRead(path: string): Promise<ProtectedRead> {
  try {
    const token = (await cookies()).get(COOKIE)?.value;
    if (!token) return { ok: false, reason: "SIGN_IN" };
    const response = await upstream(path, "GET", { "X-Operator-Session": token }, undefined, 131072);
    if (response.status === 401) return { ok: false, reason: "SIGN_IN" };
    if (response.status === 409) return { ok: false, reason: "CHANGED" };
    return response.value ? { ok: true, value: response.value } : { ok: false, reason: "UNAVAILABLE" };
  } catch { return { ok: false, reason: "UNAVAILABLE" }; }
}

export async function readDossiers(offset = 0, head?: string): Promise<ProtectedRead> {
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 1000 || (head !== undefined && !/^[a-f0-9]{64}$/.test(head)) || (offset > 0 && head === undefined)) return { ok:false, reason:"INVALID_PAGE" };
  return protectedRead(`/api/v1/research/candidates/page?limit=2&offset=${offset}${head ? `&head=${head}` : ""}`);
}

export async function readGenomes(offset = 0, head?: string): Promise<ProtectedRead> {
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 1000 || (head !== undefined && !/^[a-f0-9]{64}$/.test(head)) || (offset > 0 && head === undefined)) return {ok:false,reason:"INVALID_PAGE"};
  return protectedRead(`/api/v1/evolution/genomes/page?limit=10&offset=${offset}${head ? `&head=${head}` : ""}`);
}

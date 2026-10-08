"use client";

import { useEffect, useState, useTransition, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { publicSession, record, type PublicSession } from "./boundary";
import { type Action, type Command } from "./pending-action";
import { usePendingAction, retainAction, resolveAction } from "./pending-action-store";
import { parseOutcome, type Outcome } from "./control-outcome";

async function call(path: string, method: string, body?: unknown): Promise<unknown> {
  const response = await fetch(path, { method, cache: "no-store", headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(6000) });
  const input = record(await response.json());
  if (!response.ok) throw new Error(typeof input.detail === "string" ? input.detail : "Operator service unavailable");
  return input;
}

export function OperatorConsole({ initialSession, safetyState }: { initialSession: PublicSession | null; safetyState: string | null }) {
  const router = useRouter();
  const [session, setSession] = useState(initialSession);
  const [action, setAction] = useState<Action>("NO_NEW_RISK");
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const { command: pending, issue: checkpointIssue } = usePendingAction();
  const [receipt, setReceipt] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [refreshing, refresh] = useTransition();
  useEffect(() => {
    if (!session) return;
    const timeout = setTimeout(() => { setSession(null); setMessage("Session expired. Sign in again to check receipts or use controls."); }, Math.max(0, Date.parse(session.expires_at) - Date.now()));
    return () => clearTimeout(timeout);
  }, [session]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget, credential = String(new FormData(form).get("credential") ?? "");
    form.reset(); setBusy(true); setMessage("");
    try { setSession(publicSession(await call("/api/operator/session", "POST", { credential }))); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Sign-in failed"); }
    finally { setBusy(false); }
  }
  async function signOut() {
    setBusy(true);
    try { await call("/api/operator/session", "DELETE"); setSession(null); setMessage("Signed out."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Sign-out could not be confirmed"); }
    finally { setBusy(false); }
  }
  function acknowledge(value: unknown, command: Command) {
    const result = parseOutcome(record(value), command.action_id);
    if (result.decision === "PENDING") {
      setMessage("Backend pending: original command reserved. No applied safety change is confirmed. Check the receipt again.");
      return;
    }
    resolveAction(command.action_id);
    setReceipt(result); setReason(""); setConfirmation("");
    setMessage(""); refresh(() => router.refresh());
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !safetyState || busy || checkpointIssue) return;
    const form = event.currentTarget, credential = String(new FormData(form).get("reauth") ?? "");
    const password = form.elements.namedItem("reauth");
    if (password instanceof HTMLInputElement) password.value = "";
    const command = pending ?? { action_id: crypto.randomUUID(), requested_action: action, reason,
      operator_session_id: session.session_id, client_timestamp: new Date().toISOString(), expected_current_state: safetyState, confirmation };
    try { retainAction(command); }
    catch { setMessage("Cannot retain this action in browser storage. No command was sent."); return; }
    setBusy(true); setMessage("Awaiting backend receipt. Safety state has not been confirmed.");
    try { acknowledge(await call("/api/operator/controls", "POST", { ...command, credential }), command); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Outcome unknown. Check this action receipt before retrying."); }
    finally { setBusy(false); }
  }
  async function checkReceipt() {
    if (!pending) return;
    setBusy(true);
    try { acknowledge(await call("/api/operator/controls/" + pending.action_id, "GET"), pending); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Receipt unavailable. Keep the action identity."); }
    finally { setBusy(false); }
  }
  const selectedAction = pending?.requested_action ?? action;
  const shownReason = pending?.reason ?? reason;
  const shownConfirmation = pending?.confirmation ?? confirmation;
  const confirmed = selectedAction === "NO_NEW_RISK" || shownConfirmation === selectedAction;
  return <section className="panel content-panel operator-console" aria-labelledby="operator-controls-heading">
    <div className="panel-heading"><h2 id="operator-controls-heading">Paper safety controls</h2><span>OPERATOR SESSION</span></div>
    <p className="panel-muted">No new risk blocks new exposure. Flatten only permits exposure reduction; it does not liquidate positions. Halt stops paper activity. Recovery requires a separate gated workflow.</p>
    {!session ? <form className="operator-login" onSubmit={signIn}>
      <label htmlFor="operator-access">Operator access code</label>
      <input id="operator-access" name="credential" type="password" autoComplete="off" required maxLength={512} disabled={busy} />
      <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form> : <div className="operator-session-status"><span>Signed in until {new Date(session.expires_at).toLocaleTimeString()}. Sessions expire after 15 minutes.</span><button type="button" onClick={signOut} disabled={busy}>Sign out</button></div>}
    <form className="operator-control-form" onSubmit={submit}>
      <label htmlFor="operator-action">Paper safety action</label>
      <select id="operator-action" name="action" value={selectedAction} onChange={(event) => { setAction(event.target.value as Action); setConfirmation(""); }} disabled={busy || !!pending}>
        <option value="NO_NEW_RISK">No new risk</option><option value="FLATTEN_ONLY">Flatten only</option><option value="HALT">Halt</option>
      </select>
      <label htmlFor="operator-reason">Reason for action</label>
      <textarea id="operator-reason" name="reason" value={shownReason} onChange={(event) => setReason(event.target.value)} required maxLength={512} disabled={busy || !!pending} />
      {selectedAction !== "NO_NEW_RISK" && <><label htmlFor="operator-confirm">Type {selectedAction} to confirm</label><input id="operator-confirm" name="confirmation" value={shownConfirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" disabled={busy || !!pending} /></>}
      <label htmlFor="operator-reauth">Re-enter access code</label>
      <input id="operator-reauth" name="reauth" type="password" autoComplete="off" required maxLength={512} disabled={busy || !session} />
      <p className="panel-muted">Expected backend state: {safetyState ?? "Unknown; actions disabled"}. Each action requires your access code again.</p>
      <div className="operator-buttons"><button type="submit" disabled={busy || refreshing || !session || !safetyState || !!checkpointIssue || !shownReason.trim() || !confirmed || (!!pending && pending.operator_session_id !== session.session_id)}>
        {pending ? "Retry same action" : "Apply paper safety action"}
      </button>{pending && <button type="button" disabled={busy || !session} onClick={checkReceipt}>Check receipt</button>}</div>
    </form>
    <div role="status" aria-live="polite" className="operator-message">{refreshing ? "Refreshing the authoritative safety state…" : checkpointIssue ?? message}</div>
    {pending && <p className="panel-muted">Unconfirmed action: <span className="mono">{pending.action_id}</span>. Retry keeps this identity. A missing receipt does not prove the action failed.</p>}
    {receipt && <div className="operator-receipt"><strong>{receipt.decision === "ACCEPTED" ? "Backend accepted: " : "Backend rejected: "}{receipt.decision === "REJECTED" ? receipt.rejection_reason : receipt.authoritative_state}</strong><p>Receipt <span className="mono">{receipt.receipt_id}</span></p><p>{new Date(receipt.server_timestamp).toLocaleString()} · <Link href="/audit">View audit trail</Link></p></div>}
  </section>;
}

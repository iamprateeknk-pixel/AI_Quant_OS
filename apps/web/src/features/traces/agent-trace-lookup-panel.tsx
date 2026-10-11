import Link from "next/link";
import {advisoryRunPattern, parseAgentTraceLookup} from "./agent-trace-lookup";

export function AgentTraceLookupPanel({runId, result}: {runId: string; result: {ok: true; value: unknown} | {ok: false; reason: string} | null}) {
  const valid = advisoryRunPattern.test(runId);
  const snapshot = result?.ok && valid ? parseAgentTraceLookup(result.value, runId) : null;
  const signIn = result && !result.ok && result.reason === "SIGN_IN";
  const binding = snapshot?.binding;
  const message = !result ? "Enter an advisory run ID to inspect its saved trace context." : signIn ? "Sign in to inspect this advisory run." : !valid ? "Enter a canonical advisory run ID." : snapshot?.state === "NOT_FOUND" ? "No advisory run was found in the observed audit snapshot." : snapshot?.state === "NOT_RECORDED" ? "No trace context was recorded for this advisory run." : "Trace context is unavailable. No IDs or audit prefix are inferred.";
  return <section className="panel content-panel" aria-labelledby="advisory-trace-heading">
    <div className="panel-heading"><h2 id="advisory-trace-heading">Advisory run trace</h2><span role="status">{snapshot?.state === "AVAILABLE" ? "Saved trace context" : snapshot?.state ?? (result ? "UNAVAILABLE" : "LOOKUP")}</span></div>
    <p className="panel-muted">Use the advisory run ID returned with a classification decision. Saved graph execution IDs below belong to a separate archive.</p>
    <form method="get" action="/traces" className="health-navigation"><label htmlFor="advisory-run-id">Advisory run ID</label><input id="advisory-run-id" name="run" type="text" required maxLength={36} pattern="[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}" defaultValue={valid ? runId : ""} autoComplete="off" spellCheck={false}/><button type="submit" className="health-link">Look up saved trace</button></form>
    {binding ? <dl className="fact-list research-provenance"><div><dt>SDK trace ID</dt><dd className="mono">{binding.sdk_trace_id}</dd></div><div><dt>Archive span ID</dt><dd className="mono">{binding.sdk_span_id}</dd></div><div><dt>Original runtime</dt><dd className="mono">{binding.runtime_id}</dd></div><div><dt>Installed SDK</dt><dd>{binding.sdk_version}</dd></div><div><dt>Recorded (UTC)</dt><dd><time dateTime={binding.recorded_at}>{binding.recorded_at}</time></dd></div><div><dt>Original run commitment</dt><dd className="mono">{binding.run_sha256}</dd></div></dl> : <div className="empty-list" role="status">{message}</div>}
    {snapshot?.source_head && <details className="research-details"><summary>Inspect observed audit snapshot</summary><dl className="fact-list research-provenance"><div><dt>Audit head</dt><dd className="mono">{snapshot.source_head}</dd></div><div><dt>Recorded events</dt><dd>{snapshot.source_event_count}</dd></div><div><dt>Read (UTC)</dt><dd><time dateTime={snapshot.observed_at}>{snapshot.observed_at}</time></dd></div></dl></details>}
    <p className="panel-muted">Saved context is not proof of delivery, a complete trace or independent attestation. This read does not flush, retry or change trading controls.</p>
    {signIn && <Link className="health-link" href="/risk">Sign in at Risk Center</Link>}
  </section>;
}

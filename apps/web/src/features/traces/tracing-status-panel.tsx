import Link from "next/link";
import {parseTracingStatus} from "./tracing-status";

export function TracingStatusPanel({result}: {result: {ok: true; value: unknown} | {ok: false; reason: string}}) {
  const snapshot = result.ok ? parseTracingStatus(result.value) : null;
  const signIn = !result.ok && result.reason === "SIGN_IN";
  const available = snapshot && !["DISABLED", "UNAVAILABLE"].includes(snapshot.runtime_state);
  return <section className="panel content-panel" aria-labelledby="trace-export-heading">
    <div className="panel-heading"><h2 id="trace-export-heading">Trace export diagnostics</h2><span role="status">{snapshot?.runtime_state ?? "UNAVAILABLE"}</span></div>
    <p className="panel-muted">Counts cover finished spans in one running session and do not survive a restart. An exporter acknowledgement is not proof of durable delivery or a complete trace.</p>
    {available && snapshot ? <>
      {snapshot.runtime_state === "STOPPING" && <p className="notice warning" role="status">Export admission has stopped; the original worker may still be finishing an export or cleanup.</p>}
      {snapshot.runtime_state === "FAILED" && <p className="notice warning" role="status">Export work failed. Original outcomes are retained; new spans are dropped. This observation does not change risk controls.</p>}
      {(snapshot.dropped_spans! > 0 || snapshot.unconfirmed_spans! > 0 || snapshot.instrumentation_errors! > 0) && <p className="notice warning" role="status">Some trace work was lost, unconfirmed or could not be recorded. Counts do not identify every affected trace.</p>}
      <dl className="fact-list"><div><dt>Snapshot read (UTC)</dt><dd><time dateTime={snapshot.observed_at}>{snapshot.observed_at}</time></dd></div><div><dt>Original worker</dt><dd>{snapshot.worker_alive ? "Original worker observed alive" : "Original worker observed terminated"}</dd></div><div><dt>Span recording errors</dt><dd>{snapshot.instrumentation_errors}</dd></div><div><dt>Capacity</dt><dd>512 queued spans / 32 per export batch</dd></div></dl>
      <div className="table-scroll"><table><caption>Original session trace outcomes at snapshot time</caption><thead><tr><th scope="col">Outcome</th><th scope="col">Count</th></tr></thead><tbody>{([
        ["Finished spans observed", snapshot.observed_spans], ["Exporter acknowledged", snapshot.acknowledged_spans],
        ["Unconfirmed export outcome", snapshot.unconfirmed_spans], ["Dropped", snapshot.dropped_spans],
        ["Pending in queue", snapshot.pending_spans], ["In flight", snapshot.inflight_spans],
        ["Acknowledged batches", snapshot.acknowledged_batches], ["Failed batches", snapshot.failed_batches],
      ] as const).map(([label, count]) => <tr key={label}><th scope="row">{label}</th><td>{count}</td></tr>)}</tbody></table></div>
      <details className="research-details"><summary>Inspect original session identity and exporter</summary><dl className="fact-list research-provenance"><div><dt>Runtime identity</dt><dd className="mono">{snapshot.runtime_id}</dd></div><div><dt>Installed SDK</dt><dd>{snapshot.sdk_version}</dd></div><div><dt>Exporter</dt><dd>{snapshot.exporter_kind === "INJECTED" ? "Caller supplied exporter" : "Local OTLP protobuf"}</dd></div></dl></details>
      <p className="panel-muted">This is a point-in-time read. The current request finishes after these counts are captured. Refresh reads a later snapshot; it does not flush or retry exports. Compare counts only within the same runtime identity.</p>
    </> : <div className="empty-list" role="status">{signIn ? "Sign in to view protected trace diagnostics. No counts are inferred." : snapshot?.runtime_state === "DISABLED" ? "Trace export is disabled. No counts or worker state are inferred." : "Trace diagnostics are unavailable. No counts or worker state are inferred."}</div>}
    <div className="health-navigation">{signIn ? <Link className="health-link" href="/risk">Sign in at Risk Center</Link> : <form method="get" action="/traces"><button type="submit" className="health-link">Refresh trace diagnostics</button></form>}</div>
  </section>;
}

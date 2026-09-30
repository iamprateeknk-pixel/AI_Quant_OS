import { getAuditIntegrity } from "@/lib/control-api";

export async function AuditIntegrityPanel() {
  const result = await getAuditIntegrity();
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Local audit integrity</h2><span>{result.ok ? result.value.valid ? "VERIFIED" : "FAILED" : "UNAVAILABLE"}</span></div>
    {result.ok ? <dl className="fact-list">
      <div><dt>Chained events</dt><dd>{result.value.event_count}</dd></div>
      <div><dt>Legacy events baselined</dt><dd>{result.value.legacy_event_count}</dd></div>
      <div><dt>Verification</dt><dd>{result.value.valid ? "History matches stored chain" : "History has changed; commands are unavailable"}</dd></div>
    </dl> : <div className="empty-list">Audit integrity service unavailable.</div>}
    <p className="panel-muted">This check detects changes to local records. Independent immutable storage is still required for production. Legacy events can be verified only from the time they were baselined.</p>
  </section>;
}

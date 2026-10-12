import Link from "next/link";

export default function PaperMonitoringRunbook() {
  return <div className="page-content">
    <div className="page-heading"><div><p className="eyebrow">Incidents</p><h1>Paper monitoring recovery</h1><p>Guidance for the admitted local paper metrics source. Safety controls and recovery remain backend-authoritative.</p></div></div>
    <section className="panel content-panel" id="scrape-failed" aria-labelledby="scrape-failed-heading"><div className="panel-heading"><h2 id="scrape-failed-heading">Metrics scrape failed</h2></div>
      <div className="empty-list"><p>An existing source continuously reported a failed scrape for two minutes of rule evaluation. The hold filters brief development outages; it is not a trading service guarantee.</p><ol>
        <li>Retain the alert state, original evaluation and active times, and known source identity. Inspect that exact admitted target’s last scrape and error in the private monitoring tools.</li>
        <li>Check current backend status and order inventory. Older metric history is not current source health. Do not retry an order to test monitoring.</li>
        <li>For rejected authorization, inspect the privately provisioned scraper credential and its file access under the established credential procedure. Keep credentials out of URLs, dashboards, command arguments and tickets.</li>
        <li>For connection errors, confirm the original owned listener and monitoring process. For an unavailable metrics owner, inspect its configured state. Retain parser, size or timeout diagnostics privately. Do not reset data, relax authorization or restart an unknown process to make the view green.</li>
        <li>If safety needs a control change, use the authorized Risk Center workflow and verify backend acknowledgement. An alert cannot issue or acknowledge that command.</li>
      </ol><p>Close the monitoring investigation after this original source has fresh successful scrapes, expected metrics, healthy evaluations and an inactive rule. Record the cause and correction, and separately verify safety and reconciliation gates. Clearing an alert never resumes risk.</p></div>
    </section>
    <section className="panel content-panel" id="scrape-missing" aria-labelledby="scrape-missing-heading"><div className="panel-heading"><h2 id="scrape-missing-heading">Metrics observation missing</h2></div>
      <div className="empty-list"><p>No selectable observation for the configured paper source has existed for two minutes of rule evaluation. A present failed scrape belongs to the procedure above.</p><ol>
        <li>Confirm the original monitoring process and its loaded rule group are healthy, and inspect the exact admitted target inventory and configuration privately.</li>
        <li>Compare the job, target address and metrics path with the admitted configuration. A healthy unrelated source or retained historical sample does not replace this source.</li>
        <li>Check the source timestamp and stale markers. Without a stale marker, monitoring lookback may still select an older sample. The hold starts when absence is observed, not when the backend stopped.</li>
        <li>Preserve the original database, logs and incident times. Correct only a confirmed source or configuration problem through its established admission procedure. Do not reset a journal, queue or unknown process.</li>
      </ol><p>Resolve the monitoring investigation after the exact source again has fresh successful observations, healthy rule evaluation and an inactive rule. Presence alone does not establish successful ingestion.</p></div>
    </section>
    <section className="panel content-panel"><div className="panel-heading"><h2>Delivery and recovery limits</h2></div><div className="empty-list"><p>These rule states are local observations. They can clear automatically and may reset after a monitoring restart. Durable incident acknowledgement, notification delivery and engine-health coverage are not verified. A stopped monitoring engine cannot evaluate its own missing-source alert.</p><p>This guide provides no automatic repair or trading recovery authority. Keep original evidence and complete each existing approval, safety and reconciliation gate.</p><Link className="health-link" href="/incidents" prefetch={false}>Return to incident observations</Link><br/><Link className="health-link" href="/risk" prefetch={false}>Open Risk Center</Link></div></section>
  </div>;
}

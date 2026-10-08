import Link from "next/link";
import { parseHealth, parseTimeline } from "./timeline";

type Read = { ok: true; value: unknown } | { ok: false; reason: string };

function ReadIssue({ reason }: { reason: string }) {
  const message = reason === "SIGN_IN" ? "Sign in to view protected health records." :
    reason === "CHANGED" ? "The incident journal changed. Reload the first page to view the new timeline." :
    reason === "INVALID_PAGE" ? "This incident page cannot be opened. Start at the first page." :
    "Verified health records are unavailable. No health verdict is inferred.";
  return <div className="empty-list" role="status"><p>{message}</p>
    <Link className="health-link" href={reason === "SIGN_IN" ? "/risk" : "/incidents"}>{reason === "SIGN_IN" ? "Sign in at Risk Center" : "Reload first page"}</Link>
  </div>;
}

export function HealthPanel({ statusResult, timelineResult, observedAt }: { statusResult: Read; timelineResult: Read; observedAt: string }) {
  const health = statusResult.ok ? parseHealth(statusResult.value) : null;
  const timeline = timelineResult.ok ? parseTimeline(timelineResult.value) : null;
  const tickAge = health?.last_tick ? Date.parse(observedAt) - Date.parse(health.last_tick) : null;
  const tickState = tickAge === null ? "No scan timestamp available" : tickAge < 0 ? "Scan timestamp is ahead of this view" : tickAge > 60000 ? "Last scan is older than 60 seconds" : "Last scan within 60 seconds";
  return <>
    <section className="panel content-panel">
      <div className="panel-heading"><h2>Health monitoring</h2><span>{health?.state ?? "UNAVAILABLE"}</span></div>
      {health ? <>
        <dl className="fact-list">
          <div><dt>Background monitoring</dt><dd>{health.enabled ? "Enabled" : "Disabled"}</dd></div>
          <div><dt>Recorded open incidents</dt><dd>{health.open_incidents ?? "Unavailable"}</dd></div>
          <div><dt>Last scan, UTC</dt><dd>{health.last_tick ?? "Unavailable"}</dd></div>
          <div><dt>Scan freshness</dt><dd>{tickState}</dd></div>
          <div><dt>Last monitoring error</dt><dd>{health.state === "DISABLED" ? "Unavailable" : health.last_error ?? "None recorded"}</dd></div>
        </dl>
        <p className="panel-muted">{health.state === "DISABLED" ? "No health journal is connected. Disabled monitoring does not mean the system is healthy." : health.state === "RESTORED_ONLY" ? "Recorded incidents and quarantine restrictions were restored. Background scanning is disabled." : "Monitoring state is an observation, not a release or recovery approval."} This read-only view was fetched at {observedAt}. Refresh to obtain another observation.</p>
        <div className="health-navigation"><Link className="health-link" href="/incidents">Refresh health records</Link><Link className="health-link" href="/risk">View authoritative risk state</Link></div>
      </> : <ReadIssue reason={statusResult.ok ? "INVALID_RESPONSE" : statusResult.reason} />}
    </section>
    {health && health.state !== "DISABLED" && <section className="panel content-panel">
      <div className="panel-heading"><h2>Retained quarantine restrictions</h2><span>{health.quarantined_versions.length} VERSIONS</span></div>
      {health.quarantined_versions.length ? <ul className="component-list">{health.quarantined_versions.map((pin) => <li key={pin}><strong>{pin}</strong><Link className="health-link" href={`/agents#${pin.replace("@", "-")}`}>View registered contract</Link></li>)}</ul> : <div className="empty-list">No quarantine versions recorded in this observation.</div>}
      <p className="panel-muted">Quarantine remains in force when scanning is disabled. Recovery requires the separately gated backend workflow.</p>
    </section>}
    {health && health.state !== "DISABLED" && <section className="panel content-panel">
      <div className="panel-heading"><h2>Verified incident timeline</h2><span>{timeline ? `${timeline.total} RECORDED` : "UNAVAILABLE"}</span></div>
      {timeline ? <>
        {timeline.incidents.length ? <div className="table-scroll"><table className="health-timeline-table">
          <caption className="sr-only">Retained paper incidents, newest observation first</caption>
          <thead><tr><th scope="col">Detected, UTC</th><th scope="col">Failure</th><th scope="col">Required safety</th><th scope="col">Status</th><th scope="col">Original records</th></tr></thead>
          <tbody>{timeline.incidents.map((incident) => <tr key={incident.incident_id}>
            <td><time dateTime={incident.detected_at}>{incident.detected_at}</time></td><td>{incident.code}</td><td>{incident.safety_state}</td><td>{incident.status}</td>
            <td><details className="health-record"><summary>Inspect incident {incident.incident_id.slice(0, 12)}</summary><dl className="fact-list">
              <div><dt>Subject</dt><dd>{incident.subject}</dd></div><div><dt>Incident ID</dt><dd className="mono">{incident.incident_id}</dd></div>
              <div><dt>Agent version</dt><dd>{incident.agent_id ? <Link className="health-link" href={`/agents#${incident.agent_id}-${incident.agent_version}`}>{incident.agent_id}@{incident.agent_version}</Link> : "Not an agent incident"}</dd></div>
              <div><dt>Manifest reference</dt><dd className="mono">{incident.manifest_ref ?? "Not applicable"}</dd></div>
              <div><dt>Quarantine</dt><dd>{incident.quarantine ? "Required" : "Not an agent incident"}</dd></div>
            </dl></details></td>
          </tr>)}</tbody>
        </table></div> : <div className="empty-list">{timeline.total === 0 ? "No incidents recorded in the verified journal." : "No records on this page. Reload the first page."}</div>}
        <div className="health-navigation"><span>Showing {timeline.incidents.length ? timeline.offset + 1 : 0}–{timeline.incidents.length ? timeline.offset + timeline.incidents.length : 0} of {timeline.total}</span>
          {timeline.offset > 0 && <Link className="health-link" href={`/incidents?offset=${Math.max(0, timeline.offset - timeline.limit)}&head=${timeline.head_hash}`}>Previous incidents</Link>}
          {timeline.next_offset !== null && <Link className="health-link" href={`/incidents?offset=${timeline.next_offset}&head=${timeline.head_hash}`}>Older incidents</Link>}
        </div>
        <p className="panel-muted mono health-head">Verified journal head: {timeline.head_hash}</p>
        <p className="panel-muted">An incident’s required safety state is historical policy evidence; current safety appears in Risk Center. Incidents remain OPEN until a separately admitted resolution workflow exists. Raw captured evidence is withheld from this view.</p>
      </> : <ReadIssue reason={timelineResult.ok ? "INVALID_RESPONSE" : timelineResult.reason} />}
    </section>}
  </>;
}

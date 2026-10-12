import Link from "next/link";
import {parseMonitoringAlerts} from "./monitoring-alerts";

type Read = {ok: true; value: unknown} | {ok: false; reason: string};
const labels = {INACTIVE:"Inactive",PENDING:"Pending",FIRING:"Firing"};
export function MonitoringAlertPanel({result,observedAt,automatic=false}:{result:Read;observedAt:string;automatic?:boolean}) {
  const snapshot = result.ok ? parseMonitoringAlerts(result.value) : null;
  const age = snapshot ? Date.parse(observedAt)-Date.parse(snapshot.observed_at) : NaN;
  const current = snapshot?.state === "AVAILABLE" && Number.isFinite(age) && age >= 0 && age <= 45000;
  const signIn = !result.ok && result.reason === "SIGN_IN";
  return <section className="panel content-panel" aria-labelledby="monitoring-alert-heading">
    <div className="panel-heading"><h2 id="monitoring-alert-heading">Paper metrics alerts</h2><span>{current ? "READ SNAPSHOT" : snapshot?.state === "DISABLED" ? "DISABLED" : "UNAVAILABLE"}</span></div>
    <p className="panel-muted">Point-in-time monitoring observations. Pending means the two-minute noise hold has not elapsed; firing means the rule remains active. Inactive does not establish healthy orders or safe risk recovery. Notification delivery and durable acknowledgement are not verified.</p>
    {current && snapshot ? <>
      <dl className="fact-list"><div><dt>Snapshot read, UTC</dt><dd><time dateTime={snapshot.observed_at}>{snapshot.observed_at}</time></dd></div><div><dt>Source evaluation window</dt><dd>Within 45 seconds of the snapshot read</dd></div></dl>
      <div className="table-scroll"><table><caption>Selected local paper monitoring rules at snapshot time</caption><thead><tr><th scope="col">Observation</th><th scope="col">Rule state</th><th scope="col">Last evaluated, UTC</th><th scope="col">Active since, UTC</th><th scope="col">Recovery guidance</th></tr></thead><tbody>{snapshot.rules.map(rule=><tr key={rule.name}>
        <th scope="row">{rule.name === "PaperHttpScrapeFailed" ? "Metrics scrape failed" : "Metrics observation missing"}</th><td>{labels[rule.state]}</td><td><time dateTime={rule.evaluated_at}>{rule.evaluated_at}</time></td><td>{rule.active_since ? <time dateTime={rule.active_since}>{rule.active_since}</time> : "No active rule observed"}</td><td><Link className="health-link" href={`/incidents/runbook#${rule.name === "PaperHttpScrapeFailed" ? "scrape-failed" : "scrape-missing"}`} prefetch={false}>Open runbook</Link></td>
      </tr>)}</tbody></table></div>
      <p className="panel-muted">{automatic?"Updates require a fresh authenticated stream and protected monitoring read.":"This view does not update automatically. Refresh for a new observation."} A rule can clear without an operator acknowledgement; it never resumes trading or resolves retained health incidents.</p>
    </> : <div className="empty-list" role="status">{signIn ? "Sign in to view protected monitoring observations." : snapshot?.state === "DISABLED" ? "The monitoring source is disabled. No rule states are inferred." : snapshot?.state === "AVAILABLE" && age > 45000 ? "This snapshot is older than 45 seconds. Refresh before using rule states." : "Monitoring observations are unavailable or cannot be validated. No rule states are inferred."}</div>}
    <div className="health-navigation">{signIn ? <Link className="health-link" href="/risk" prefetch={false}>Sign in at Risk Center</Link> : <form method="get" action="/incidents"><button className="health-link" type="submit">Refresh monitoring observations</button></form>}</div>
  </section>;
}

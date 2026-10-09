import Link from "next/link";
import { parseDemoSyncHealth } from "./demo-sync-health";

type Read = {ok:true;value:unknown} | {ok:false;reason:string};
const serviceLabels: Record<string,string> = {RUNNING:"Running",STOPPED:"Stopped",HALTED:"Automatic observations halted",DISABLED:"Disabled"};
const syncLabels: Record<string,string> = {STARTING:"Waiting for first sync",ACTIVE:"Sync in progress",HEALTHY:"Last sync succeeded",DEGRADED:"Waiting after a failed sync",HALTED:"Automatic observations halted",DISABLED:"Disabled"};
const reasons: Record<string,string> = {STORAGE_UNAVAILABLE:"Sync storage is unavailable",CLOCK_REGRESSION:"Clock moved backward",HISTORY_GAP:"History window requires review",CUSTODY_UNCERTAIN:"Worker custody is uncertain",SOURCE_FAILURE:"Source observation failed",PROJECTION_FAILURE:"Account-state import failed",CAPACITY:"Journal capacity reached",TICK_LIMIT:"Configured timer limit reached",SERVICE_FAILURE:"Service failed"};
export function DemoSyncPanel({result,observedAt}:{result:Read;observedAt:string}) {
  const view=result.ok ? parseDemoSyncHealth(result.value,observedAt) : null;
  const record=view?.record, health=record?.health;
  const issue=!result.ok && result.reason==="SIGN_IN" ? "Sign in to view protected demo service health." : view && !view.configured ? "Demo service health is not configured." : "Demo service health is unavailable. No connectivity status is inferred.";
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Demo account synchronization</h2><span>{view?.state==="STALE" ? "Record is stale" : record ? serviceLabels[record.state] : "Unavailable"}</span></div>
    {record ? <>
      {view?.state==="STALE" && <p className="empty-list" role="status">This record has expired. The service may have stopped; its current state is unknown.</p>}
      <dl className="fact-list">
        <div><dt>Recorded service state</dt><dd>{serviceLabels[record.state]}</dd></div>
        <div><dt>Last sync state</dt><dd>{health ? syncLabels[health.state] : "Not yet observed"}</dd></div>
        <div><dt>Recorded reason</dt><dd>{health?.code ? reasons[health.code] : record.code ? reasons[record.code] : "None recorded"}</dd></div>
        <div><dt>Published, UTC</dt><dd><time dateTime={record.published_at}>{record.published_at}</time></dd></div>
        <div><dt>Last successful cycle, UTC</dt><dd>{health?.last_success_at ?? "Not recorded"}</dd></div>
        <div><dt>History observed through, UTC</dt><dd>{health?.last_history_end ?? "Not recorded"}</dd></div>
        <div><dt>Next observation due, UTC</dt><dd>{health && health.state!=="HALTED" && record.state==="RUNNING" ? health.next_due_at : "No automatic observation scheduled"}</dd></div>
        <div><dt>Consecutive failed cycles</dt><dd>{health?.consecutive_failures ?? "Not yet observed"}</dd></div>
      </dl>
      <details className="health-record"><summary>Inspect service references</summary><dl className="fact-list">
        <div><dt>Service run</dt><dd className="mono">{record.run_id}</dd></div>
        <div><dt>Timer ticks</dt><dd>{record.ticks}</dd></div>
        <div><dt>Pending capture</dt><dd className="mono">{health?.pending_attempt_id ?? "None recorded"}</dd></div>
        {health?.pending_attempt_id && <><div><dt>Pending history start, UTC</dt><dd>{health.pending_history_start}</dd></div><div><dt>Pending history end, UTC</dt><dd>{health.pending_history_end}</dd></div></>}
      </dl></details>
    </> : <div className="empty-list" role="status"><p>{issue}</p>{!result.ok && result.reason==="SIGN_IN" && <Link className="health-link" href="/risk">Sign in at Risk Center</Link>}</div>}
    <p className="panel-muted">This is a local service observation. Native account freshness remains unverified. History completeness and terminal ownership are not certified. Order submission and portfolio changes remain disabled for this service.</p>
    <div className="health-navigation"><Link className="health-link" href="/brokers">Refresh broker observations</Link><span className="panel-muted">View fetched at {observedAt}</span></div>
  </section>;
}

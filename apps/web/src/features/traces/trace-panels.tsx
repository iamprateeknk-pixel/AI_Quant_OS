import Link from "next/link";
import {parseGraphRunPage,parseGraphTrace} from "./graph-traces";

type ReadResult = {ok:true;value:unknown}|{ok:false;reason:string};
const labels:Record<string,string> = {CREATED:"Reserved",RUNNING:"Running",COMPLETED:"Completed",UNAVAILABLE:"Implementation unavailable",EXPIRED:"Expired",DENIED:"Permission denied",WAIT_FOR_DATA:"Waiting for data",INVALID:"Invalid output",FAILED:"Failed",TIMEOUT:"Timed out",UNCERTAIN:"Outcome uncertain"};
const utc = (value:string) => value.replace("T"," ").replace(/(?:Z|\+00:00)$/," UTC");

function Unavailable({reason}:{reason:string}) {
  const message = reason === "SIGN_IN" ? "Sign in to view saved agent executions." : reason === "CHANGED" ? "The execution list changed. Reload it to continue." : reason === "NOT_FOUND" ? "This saved execution was not found." : reason === "INVALID_PAGE" || reason === "INVALID_TRACE" ? "This trace link is invalid. Open the execution list again." : "Saved execution evidence is unavailable. Reload to try again.";
  return <div className="empty-list" role="status"><p>{message}</p><Link href={reason === "SIGN_IN" ? "/settings" : "/traces"} prefetch={false}>{reason === "SIGN_IN" ? "Open owner sign-in" : "Reload execution list"}</Link></div>;
}

export function GraphRunListPanel({result,after="",head}:{result:ReadResult;after?:string;head?:string}) {
  const page = result.ok ? parseGraphRunPage(result.value,after,head) : null;
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Saved agent executions</h2><span>{page ? `${page.total} SAVED` : "UNAVAILABLE"}</span></div>
    <p className="panel-muted">Original paper and sandbox runs, ordered by execution ID. Open a run to inspect its saved transitions. Context observation is when the input snapshot was prepared; it is separate from execution timing.</p>
    {page ? page.records.length ? <>
      <div className="table-scroll"><table className="graph-run-table"><caption className="sr-only">Saved agent executions and original context observations</caption><thead><tr><th scope="col">Execution</th><th scope="col">Agent / version</th><th scope="col">Task</th><th scope="col">Outcome</th><th scope="col">Scope</th><th scope="col">Context observed (UTC)</th></tr></thead><tbody>{page.records.map(row=><tr key={row.run_id}><th scope="row" className="trace-reference"><Link href={`/traces/${encodeURIComponent(row.run_id)}`} prefetch={false}>{row.run_id}</Link></th><td>{row.agent_id}<br/><span className="mono">{row.agent_version}</span></td><td className="trace-reference">{row.task_id}</td><td>{labels[row.status]}</td><td>{row.scope === "SANDBOX" ? "Sandbox" : "Paper"}</td><td><time dateTime={row.context_observed_at}>{utc(row.context_observed_at)}</time></td></tr>)}</tbody></table></div>
      <nav className="trace-pagination" aria-label="Execution pages"><Link href="/traces" prefetch={false}>Reload first page</Link>{page.next_after && <Link href={`/traces?after=${encodeURIComponent(page.next_after)}&head=${page.head_hash}`} prefetch={false}>Next executions</Link>}</nav>
    </> : <div className="empty-list" role="status"><p>{page.total ? "No more executions on this page." : "No agent executions saved."}</p><Link href="/traces" prefetch={false}>Reload execution list</Link></div> : <Unavailable reason={result.ok ? "UNAVAILABLE" : result.reason}/>}
  </section>;
}

export function GraphTracePanel({result,runId}:{result:ReadResult;runId:string}) {
  const trace = result.ok ? parseGraphTrace(result.value,runId) : null;
  const timingLabels = {AVAILABLE:"Timing recorded",PARTIAL:"Timing incomplete",UNAVAILABLE:"Timing not recorded",CLOCK_REGRESSION:"Clock changed"};
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Agent execution timeline</h2><span>{trace ? labels[trace.status] : "UNAVAILABLE"}</span></div>
    {trace ? <>
      <dl className="fact-list"><div><dt>Agent / version</dt><dd>{trace.agent_id} · {trace.agent_version}</dd></div><div><dt>Task</dt><dd className="trace-reference">{trace.task_id}</dd></div><div><dt>Scope</dt><dd>{trace.scope === "SANDBOX" ? "Sandbox" : "Paper"}</dd></div><div><dt>Context observed (UTC)</dt><dd><time dateTime={trace.context_observed_at}>{utc(trace.context_observed_at)}</time></dd></div><div><dt>Context valid until (UTC)</dt><dd><time dateTime={trace.context_valid_until}>{utc(trace.context_valid_until)}</time></dd></div><div><dt>Timing evidence</dt><dd>{timingLabels[trace.timing_state]}</dd></div><div><dt>Observed reservation interval</dt><dd>{trace.elapsed_seconds === null ? "Unavailable" : `${trace.elapsed_seconds} seconds`}</dd></div></dl>
      <p className="panel-muted">The reservation interval runs from the recorded start to the terminal observation. It is wall-clock evidence, not processor time or the exact completion time of an uncertain external operation. Missing or regressing observations withhold the interval. This saved trace does not establish current eligibility or authorize risk.</p>
      <div className="table-scroll"><table><caption className="sr-only">Original execution transitions with UTC observations</caption><thead><tr><th scope="col">Transition</th><th scope="col">Observed time (UTC)</th><th scope="col">Audit sequence</th></tr></thead><tbody>{trace.events.map(event=><tr key={event.sequence}><th scope="row">{labels[event.status]}</th><td>{event.observed_at ? <time dateTime={event.observed_at}>{utc(event.observed_at)}</time> : "Not recorded"}</td><td>{event.sequence}</td></tr>)}</tbody></table></div>
      <details className="agent-contract"><summary>Original references and fingerprints</summary><dl className="fact-list">{[["Execution",trace.run_id],["Graph trace",trace.trace_id],["Envelope trace",trace.output_trace_id??"No completed envelope trace"],["Context",trace.context_ref],["Manifest",trace.manifest_ref],["Output fingerprint",trace.output_sha256??"No completed output"]].map(([label,value])=><div key={label}><dt>{label}</dt><dd className="mono trace-reference">{value}</dd></div>)}</dl><h3>Input references</h3><ul>{trace.input_refs.map((ref,index)=><li key={index} className="mono trace-reference">{ref}</li>)}</ul><h3>Transition fingerprints</h3><dl className="fact-list">{trace.events.map(event=><div key={event.sequence}><dt>{labels[event.status]} · {event.sequence}</dt><dd className="mono trace-reference">Record: {event.record_hash}<br/>Event: {event.event_hash}</dd></div>)}</dl></details>
    </> : <Unavailable reason={result.ok ? "UNAVAILABLE" : result.reason}/>}
  </section>;
}

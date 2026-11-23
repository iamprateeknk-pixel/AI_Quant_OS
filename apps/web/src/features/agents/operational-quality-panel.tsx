import type { AgentOperationalQualityRead, AgentOperationalQualityRow } from "./operational-quality";

function unavailableMessage(reason: string): string {
  if (reason === "SIGN_IN") return "Sign in to view archived agent outcomes.";
  if (reason === "GRAPH_RUN_CAPACITY_EXCEEDED") return "More than 512 graph runs are retained. The summary is withheld rather than showing a partial window.";
  if (reason === "INVALID_RESPONSE") return "The operational summary could not be validated. No counts are shown.";
  return "The graph archive could not be verified. No counts are shown.";
}

export function AgentOperationalQualityPanel({ result }: { result: AgentOperationalQualityRead }) {
  const quality = result.ok ? result.value : null;
  const available = quality?.availability === "AVAILABLE";
  const failedCount = (row: AgentOperationalQualityRow) =>
    row.status_counts.FAILED + row.status_counts.TIMEOUT + row.status_counts.UNCERTAIN;

  return <section className="panel content-panel" aria-labelledby="agent-operational-quality-title">
    <div className="panel-heading">
      <h2 id="agent-operational-quality-title">Archived operational outcomes</h2>
      <span>{available ? `${quality.source_receipt_count} RUNS` : quality?.availability ?? "UNAVAILABLE"}</span>
    </div>
    <p className="panel-muted">
      Counts come from verified retained graph receipts; trace timings include only available trace samples. This is operational history, not calibrated model quality, an SLA, current health, or promotion eligibility. The server summarizes at most 512 retained runs and reloads this snapshot when you refresh the page.
    </p>
    {available ? quality.rows.length ? <div className="table-scroll">
      <table>
        <caption className="sr-only">Archived operational outcome counts and trace durations by agent version</caption>
        <thead><tr>
          <th scope="col">Agent / version</th>
          <th scope="col">Runs</th>
          <th scope="col">Completed</th>
          <th scope="col">Failed / timeout / uncertain</th>
          <th scope="col">Other outcomes</th>
          <th scope="col">Completion rate</th>
          <th scope="col">Mean / p95 trace duration</th>
        </tr></thead>
        <tbody>{quality.rows.map((row) => {
          const failed = failedCount(row);
          const other = row.run_count - row.status_counts.COMPLETED - failed;
          const completion = (Number(row.completion_rate) * 100).toLocaleString(undefined, { maximumFractionDigits: 1 });
          return <tr key={`${row.agent_id}@${row.agent_version}`}>
            <th scope="row">{row.agent_id} · {row.agent_version}</th>
            <td>{row.run_count}</td>
            <td>{row.status_counts.COMPLETED}</td>
            <td>{failed}</td>
            <td>{other}</td>
            <td>{completion}%</td>
            <td>{row.timing_sample_count > 0 ? `${row.mean_duration_seconds} s / ${row.p95_duration_seconds} s · ${row.timing_sample_count} samples` : "No timing samples"}</td>
          </tr>;
        })}</tbody>
      </table>
    </div> : <div className="empty-list" role="status">No archived graph runs are available to summarize.</div> : quality?.availability === "NO_OBSERVATIONS" ?
      <div className="empty-list" role="status">No archived graph runs are available to summarize.</div> :
      <div className="empty-list" role="status">{unavailableMessage(quality?.reason ?? (result.ok ? "UNAVAILABLE" : result.reason))}</div>}
    {quality && <p className="panel-muted">Snapshot captured <time dateTime={quality.as_of}>{quality.as_of}</time>. Calibration established: no. Promotion allowed: no.</p>}
  </section>;
}

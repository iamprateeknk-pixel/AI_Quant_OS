"use client";

import {useRealtime} from "./use-realtime";

export function RealtimePanel() {
  const {frame,state}=useRealtime();
  const current=state==="CURRENT" && frame!==null;
  const label=current?"Current paper snapshot":state==="CONNECTING"?"Connecting":state==="STALE"?"Snapshot expired":state==="WITHHELD"?"Update withheld":"Disconnected";
  return <section className="panel content-panel" aria-labelledby="realtime-heading">
    <div className="panel-heading"><h2 id="realtime-heading">Current paper operations</h2><span role="status">{label}</span></div>
    <p className="panel-muted">Simulated paper state updates automatically while your operator session is active. Sign in through Risk Center if the feed is disconnected.</p>
    {!current && <p className="notice warning">Current state is unknown. {frame?"The table below is the last accepted observation.":"No current observation has been received."}</p>}
    {frame && <><dl className="fact-list"><div><dt>{current?"Safety state":"Last observed safety state"}</dt><dd>{frame.safety_state}</dd></div><div><dt>Recorded paper orders</dt><dd>{frame.order_count}</dd></div><div><dt>Observed at</dt><dd><time dateTime={frame.captured_at}>{frame.captured_at}</time></dd></div></dl>
      <div className="table-scroll"><table><caption>{current?"Current simulated evidence":"Last accepted simulated evidence"}</caption><thead><tr><th>Instrument</th><th>Quote</th><th>History</th><th>Bars</th><th>Regime</th></tr></thead><tbody>{frame.instruments.map(row=><tr key={row.instrument_id}><td>{row.instrument_id}</td><td>{row.quote_status}</td><td>{row.history_status}</td><td>{row.bar_count}</td><td>{row.regime}</td></tr>)}</tbody></table></div></>}
    <p className="panel-muted">This feed reports state. Safety actions still require their own backend acknowledgement. Live routing is disabled.</p>
  </section>;
}

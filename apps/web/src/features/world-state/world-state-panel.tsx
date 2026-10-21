"use client";

import {useRealtime} from "@/features/realtime/use-realtime";
import {TableScrollRegion} from "@/features/shared/table-scroll-region";

function feature(value: string | null): string {
  if (value === null) return "Unavailable";
  return value;
}

export function WorldStatePanel() {
  const {state,frame}=useRealtime(),current=state==="CURRENT" && frame!==null;
  if (!frame) return <section className="panel content-panel"><div className="panel-heading"><h2>FX world state</h2><span role="status">{state==="CONNECTING"?"Connecting":"Unavailable"}</span></div><div className="empty-list">No current paper observation has been received. Sign in through Risk Center to receive simulated world-state updates.</div></section>;
  return <>
    <section className="panel content-panel">
      <div className="panel-heading"><h2>Paper regime evidence</h2><span role="status">{current?frame.world_status:state==="STALE"?"Snapshot expired":"Update unavailable"}</span></div>
      <p className="panel-muted">Observed at <time dateTime={frame.captured_at}>{frame.captured_at}</time>. Quotes and bar history are simulated. Updates automatically while your operator session is active.</p>
      {!current && <p className="notice warning">Current world state is unknown. Retained rows describe the last accepted observation.</p>}
      {frame.world_status === "EMPTY" && <p className="panel-muted">No paper quotes were available in this observation. Regimes remain unknown until fresh quotes and enough recent bars are supplied.</p>}
      <TableScrollRegion label="Paper regime and instrument evidence table"><table><caption>{current?"Current simulated evidence":"Last accepted simulated evidence"}</caption><thead><tr><th>Instrument</th><th>Quote</th><th>History</th><th>Bars</th><th>Mid price</th><th>Spread, bps</th><th>Return, bps</th><th>Volatility, bps</th><th>Regime</th></tr></thead>
        <tbody>{frame.instruments.map((item) => <tr key={item.instrument_id}><td>{item.instrument_id}</td><td>{item.quote_status}</td><td>{item.history_status}</td><td>{item.bar_count}</td><td className="mono">{feature(item.mid_price)}</td><td className="mono">{feature(item.spread_bps)}</td><td className="mono">{feature(item.return_bps)}</td><td className="mono">{feature(item.realized_volatility_bps)}</td><td>{item.regime}</td></tr>)}</tbody>
      </table></TableScrollRegion>
      <p className="panel-muted">Regimes use fixed, uncalibrated paper thresholds. A classification carries no order approval or capital authority.</p>
    </section>
    <section className="panel content-panel"><div className="panel-heading"><h2>Currency coverage</h2><span>MACRO SOURCE PENDING</span></div>
      <TableScrollRegion label="Paper currency coverage table"><table><caption>{current?"Current recorded currency coverage":"Last accepted currency coverage"}</caption><thead><tr><th>Currency</th><th>Related pairs</th><th>Fresh quotes at observation</th><th>Macro evidence</th></tr></thead><tbody>{frame.currency_twins.map((item) => <tr key={item.currency}><td>{item.currency}</td><td>{item.instrument_ids.join(", ")}</td><td>{item.fresh_quote_count} / {item.instrument_ids.length}</td><td>{item.macro_available ? "Available" : "Unavailable"}</td></tr>)}</tbody></table></TableScrollRegion>
      <p className="panel-muted">Coverage comes from the configured pairs in this paper snapshot. Macro evidence is shown only when attached to that snapshot.</p>
    </section>
  </>;
}

import { getWorldState } from "@/lib/control-api";

function feature(value: string | null): string {
  if (value === null) return "Unavailable";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("en-US", { maximumFractionDigits: 3 }) : "Unavailable";
}

export async function WorldStatePanel() {
  const result = await getWorldState();
  if (!result.ok) return <section className="panel content-panel"><div className="panel-heading"><h2>FX world state</h2><span>UNAVAILABLE</span></div><div className="empty-list">World-state service unavailable.</div></section>;
  const state = result.value;
  return <>
    <section className="panel content-panel">
      <div className="panel-heading"><h2>Paper regime evidence</h2><span>{state.expired ? "SNAPSHOT EXPIRED" : state.status}</span></div>
      <p className="panel-muted">Snapshot {new Date(state.as_of).toISOString()}. Quotes and bar history are simulated. Reload for the latest evidence.</p>
      {state.status === "EMPTY" && <p className="panel-muted">No paper quotes are available. Regimes remain unknown until fresh quotes and enough recent bars are supplied.</p>}
      <div className="table-scroll"><table><thead><tr><th>Instrument</th><th>Quote</th><th>History</th><th>Bars</th><th>Spread, bps</th><th>Return, bps</th><th>Volatility, bps</th><th>Regime</th></tr></thead>
        <tbody>{state.instruments.map((item) => <tr key={item.instrument_id}><td>{item.instrument_id}</td><td>{item.quote_status}</td><td>{item.history_status}</td><td>{item.bar_count}</td><td className="mono">{feature(item.spread_bps)}</td><td className="mono">{feature(item.return_bps)}</td><td className="mono">{feature(item.realized_volatility_bps)}</td><td>{item.regime}</td></tr>)}</tbody>
      </table></div>
      <p className="panel-muted">Regimes use fixed, uncalibrated paper thresholds. A classification carries no order approval or capital authority.</p>
    </section>
    <section className="panel content-panel"><div className="panel-heading"><h2>Currency coverage</h2><span>MACRO SOURCE PENDING</span></div>
      <div className="table-scroll"><table><thead><tr><th>Currency</th><th>Related pairs</th><th>Fresh quotes</th><th>Macro evidence</th></tr></thead><tbody>{state.currency_twins.map((item) => <tr key={item.currency}><td>{item.currency}</td><td>{item.instrument_ids.join(", ")}</td><td>{item.fresh_quote_count} / {item.instrument_ids.length}</td><td>{item.macro_available ? "Available" : "Unavailable"}</td></tr>)}</tbody></table></div>
      <p className="panel-muted">Coverage is measured from configured pairs. Rate, inflation, policy and news sources are not connected.</p>
    </section>
  </>;
}

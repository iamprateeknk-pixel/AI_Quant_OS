import { getPositions } from "@/lib/control-api";

export async function InstrumentPositionsPanel() {
  const result = await getPositions();
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Filled positions</h2><span>PAPER LEDGER</span></div>
    {result.ok ? <div className="table-scroll"><table>
      <thead><tr><th>Instrument</th><th>Net position</th><th>Cash flow</th><th>Mark</th><th>Trading P&amp;L</th></tr></thead>
      <tbody>{result.value.positions.map((position) => <tr key={position.instrument_id}>
        <td>{position.instrument_id}</td><td className="mono">{position.net_quantity} {position.base_currency}</td>
        <td className="mono">{position.cash_flow_quote} {position.quote_currency}</td>
        <td className="mono">{position.mark_price ?? "Unavailable"}</td>
        <td className="mono">{position.marked_pnl_quote !== null ? `${position.marked_pnl_quote} ${position.quote_currency}` : "Unavailable"}</td>
      </tr>)}</tbody>
    </table></div> : <div className="empty-list">Position service unavailable.</div>}
    <p className="panel-muted">Positions and cash flows come from simulated fills. A fresh bid marks long positions and a fresh ask marks shorts. Each row shows its own currencies.</p>
  </section>;
}

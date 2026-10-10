import Link from "next/link";
import { getSystemStatus, getOrders, getCurrencyPortfolio } from "@/lib/control-api";
import { RealtimePanel } from "@/features/realtime/realtime-panel";

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="metric"><p className="metric-label">{label}</p><p className="metric-value">{value}</p><p className="metric-note">{note}</p></div>;
}

export default async function Home() {
  const [status, orders, portfolio] = await Promise.all([getSystemStatus(), getOrders(), getCurrencyPortfolio()]);
  return <div className="page-content">
    <div className="page-heading"><div><p className="eyebrow">Operator overview</p><h1>Mission Control</h1><p>Paper execution and deterministic safety controls. Live trading is disabled.</p></div><span className="mode-tag">PAPER ENVIRONMENT</span></div>
    <RealtimePanel />
    <p className="panel-muted">The page summary below was read when this page loaded. Current paper operations above receives automatic updates.</p>
    <section className="metric-strip" aria-label="Page summary">
      <Metric label="Operating mode" value={status.ok ? status.value.mode : "Unknown"} note="Backend authority" />
      <Metric label="Safety state" value={status.ok ? status.value.safety_state : "Unknown"} note="Risk kernel" />
      <Metric label="Paper currency exposure" value={portfolio.ok ? portfolio.value.gross_worst_case_exposure_reporting ?? "Unavailable" : "—"} note="Worst case gross exposure in USD" />
      <Metric label="Live positions" value="Unavailable" note="No broker connected" />
    </section>
    {!status.ok && <div className="notice warning" role="status">Control API unavailable. Start the local API to see current state.</div>}
    <div className="dashboard-grid">
      <section className="panel wide"><div className="panel-heading"><h2>Market and world state</h2><span>DATA SOURCE PENDING</span></div><div className="empty-chart"><div className="chart-grid" aria-hidden="true" /><div className="empty-chart-copy"><strong>No market feed connected</strong><p>Historical replay and live quote ingestion will populate this area.</p></div></div><div className="panel-foot"><Link href="/world-state">World State</Link><Link href="/markets/EURUSD">Market detail</Link></div></section>
      <section className="panel side-panel"><div className="panel-heading"><h2>Safety boundary</h2><span>DETERMINISTIC</span></div><dl className="fact-list"><div><dt>State at page load</dt><dd>{status.ok ? status.value.safety_state : "Unknown"}</dd></div><div><dt>Order route</dt><dd>PAPER only</dd></div><div><dt>Broker access</dt><dd>Disabled</dd></div><div><dt>Recovery control</dt><dd>Not enabled</dd></div></dl><p className="panel-muted">Risk Center provides expiring operator sessions and re-authenticated paper safety commands. Accepted actions include a durable backend receipt.</p><div className="panel-foot"><Link href="/risk">Open Risk Center</Link></div></section>
      <section className="panel wide"><div className="panel-heading"><h2>Recent paper orders</h2><Link href="/orders">View all</Link></div>{orders.ok && orders.value.orders.length > 0 ? <div className="table-scroll"><table><thead><tr><th>Intent</th><th>Order</th><th>Instrument</th><th>Route</th><th>Status</th></tr></thead><tbody>{orders.value.orders.slice(-5).reverse().map((order) => <tr key={order.order_id}><td>{order.intent_id}</td><td className="mono">{order.order_id.slice(0, 8)}</td><td>{order.instrument_id}</td><td>{order.broker_route}</td><td>{order.status}</td></tr>)}</tbody></table></div> : <div className="empty-list">{orders.ok ? "No paper orders yet." : "Order service unavailable."}</div>}<div className="panel-foot"><Link href="/portfolio">Paper portfolio</Link></div></section>
      <section className="panel side-panel"><div className="panel-heading"><h2>System components</h2><span>BUILD STATUS</span></div><ul className="component-list"><li><span>Risk kernel</span><strong>Local</strong></li><li><span>Paper gateway</span><strong>Local</strong></li><li><span>Market ingestion</span><strong>In progress</strong></li><li><span>Broker adapters</span><strong>Unavailable</strong></li><li><span>Agent runtime</span><strong>Unavailable</strong></li></ul></section>
    </div>
  </div>;
}

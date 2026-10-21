import Link from "next/link";
import { getSystemStatus, getOrders, getCurrencyPortfolio, getPositions, getFills, getMarket } from "@/lib/control-api";
import { RealtimePanel } from "@/features/realtime/realtime-panel";
import { readBrokerHealth } from "@/features/operator-session/server";
import { BrokerHealthPanel } from "@/features/brokers/health-panel";
import { TableScrollRegion } from "@/features/shared/table-scroll-region";
import { countOpenPositions } from "@/features/dashboard/metrics";

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="metric"><p className="metric-label">{label}</p><p className="metric-value">{value}</p><p className="metric-note">{note}</p></div>;
}

export default async function Home() {
  const [status, orders, portfolio, positions, fills, brokerHealth, market] = await Promise.all([getSystemStatus(), getOrders(), getCurrencyPortfolio(), getPositions(), getFills(), readBrokerHealth(), getMarket("EURUSD")]);
  const positionCount = positions.ok ? countOpenPositions(positions.value.positions) : null;
  const openPositions = positions.ok && positionCount !== null ? positions.value.positions.filter(position => countOpenPositions([position]) === 1) : null;
  const pnl = portfolio.ok && portfolio.value.valuation_available && portfolio.value.marked_pnl_reporting !== null
    ? `${portfolio.value.reporting_currency} ${portfolio.value.marked_pnl_reporting}`
    : portfolio.ok && !portfolio.value.valuation_available ? "Unvalued" : "Unavailable";
  return <div className="page-content">
    <div className="page-heading"><div><p className="eyebrow">Operator overview</p><h1>Mission Control</h1><p>Paper execution and deterministic safety controls. Live trading is disabled.</p></div><span className="mode-tag">PAPER ENVIRONMENT</span></div>
    <RealtimePanel />
    <p className="panel-muted">The page summary below was read when this page loaded. Current paper operations above receives automatic updates.</p>
    <section className="metric-strip" aria-label="Paper account summary">
      <Metric label="Operating mode" value={status.ok ? status.value.mode : "Unknown"} note="Backend authority" />
      <Metric label="Safety state" value={status.ok ? status.value.safety_state : "Unknown"} note="Risk kernel" />
      <Metric label="Paper marked P&L" value={pnl} note={portfolio.ok ? `Reporting currency: ${portfolio.value.reporting_currency}` : "Portfolio service"} />
      <Metric label="Open paper positions" value={positionCount === null ? "Unavailable" : String(positionCount)} note="Current nonzero paper positions" />
    </section>
    {!status.ok && <div className="notice warning" role="status">Control API unavailable. Start the local API to see current state.</div>}
    <BrokerHealthPanel result={brokerHealth} observedAt={new Date().toISOString()} />
    <div className="dashboard-grid">
      <section className="panel wide"><div className="panel-heading"><h2>Paper account exposure</h2><Link href="/portfolio">View portfolio</Link></div><dl className="fact-list"><div><dt>Gross worst-case exposure ({portfolio.ok ? portfolio.value.reporting_currency : "reporting currency"})</dt><dd>{portfolio.ok ? portfolio.value.gross_worst_case_exposure_reporting ?? "Unavailable" : "Unavailable"}</dd></div><div><dt>Marked P&amp;L</dt><dd>{pnl}</dd></div><div><dt>Valuation</dt><dd>{portfolio.ok ? portfolio.value.valuation_available ? "Available" : "Unavailable" : "Unavailable"}</dd></div><div><dt>Open positions</dt><dd>{positionCount === null ? "Unavailable" : positionCount}</dd></div></dl></section>
      <section className="panel side-panel"><div className="panel-heading"><h2>Safety boundary</h2><span>DETERMINISTIC</span></div><dl className="fact-list"><div><dt>State at page load</dt><dd>{status.ok ? status.value.safety_state : "Unknown"}</dd></div><div><dt>Order route</dt><dd>PAPER only</dd></div><div><dt>Native broker orders</dt><dd>Disabled</dd></div><div><dt>Gross exposure</dt><dd>{portfolio.ok ? portfolio.value.gross_worst_case_exposure_reporting ?? "Unavailable" : "Unavailable"}</dd></div></dl><p className="panel-muted">Risk Center provides expiring operator sessions and re-authenticated paper safety commands. Accepted actions include a durable backend receipt.</p><div className="panel-foot"><Link href="/risk">Open Risk Center</Link></div></section>
      <section className="panel wide"><div className="panel-heading"><h2>Open paper positions</h2><Link href="/portfolio">View all</Link></div>{openPositions?.length ? <TableScrollRegion label="Open paper positions table"><table><caption className="sr-only">Current nonzero paper positions</caption><thead><tr><th scope="col">Instrument</th><th scope="col">Net quantity</th><th scope="col">Mark price</th><th scope="col">Marked P&amp;L</th></tr></thead><tbody>{openPositions.slice(0, 10).map(position=><tr key={position.instrument_id}><th scope="row">{position.instrument_id}</th><td>{position.net_quantity}</td><td>{position.mark_price ?? "Unvalued"}</td><td>{position.marked_pnl_quote === null ? "Unvalued" : `${position.marked_pnl_quote} ${position.quote_currency}`}</td></tr>)}</tbody></table></TableScrollRegion> : <div className="empty-list" role="status">{positionCount === null ? "Paper positions or quantities are unavailable." : "No open paper positions."}</div>}</section>
      <section className="panel wide"><div className="panel-heading"><h2>Recent paper execution chain</h2><Link href="/orders">View all</Link></div>{orders.ok && orders.value.orders.length > 0 ? <TableScrollRegion label="Recent paper execution chain table"><table><caption className="sr-only">Recent paper intent, risk, order, broker acknowledgement and fill references</caption><thead><tr><th scope="col">Intent</th><th scope="col">Risk decision</th><th scope="col">Paper order</th><th scope="col">Broker acknowledgement</th><th scope="col">Instrument</th><th scope="col">Fill records</th><th scope="col">Status</th></tr></thead><tbody>{orders.value.orders.slice(-5).reverse().map(order=><tr key={order.order_id}><td className="mono">{order.intent_id}</td><td className="mono">{order.risk_id}</td><td className="mono">{order.order_id}</td><td className="mono">{order.broker_order_id ?? "Not recorded"}</td><td>{order.instrument_id}</td><td>{fills.ok ? fills.value.fills.filter(fill=>fill.order_id===order.order_id).length : "Unavailable"}</td><td>{order.status}</td></tr>)}</tbody></table></TableScrollRegion> : <div className="empty-list" role="status">{orders.ok ? "No paper orders yet." : "Paper order records are unavailable."}</div>}<p className="panel-muted">Fill records join by paper order ID. Broker reconciliation evidence remains separately gated and is not inferred here.</p></section>
      <section className="panel side-panel"><div className="panel-heading"><h2>Market and world state</h2><span>{market.ok ? market.value.freshness : "UNAVAILABLE"}</span></div>{market.ok ? <dl className="fact-list"><div><dt>Instrument</dt><dd>{market.value.instrument_id}</dd></div><div><dt>Bid</dt><dd>{market.value.bid}</dd></div><div><dt>Ask</dt><dd>{market.value.ask}</dd></div><div><dt>Source</dt><dd>{market.value.source}</dd></div><div><dt>Observed (UTC)</dt><dd><time dateTime={market.value.observed_at}>{new Date(market.value.observed_at).toLocaleString("en-GB",{timeZone:"UTC",hour12:false})}</time></dd></div><div><dt>World state version</dt><dd className="mono">{market.value.world_state_version.slice(0,12)}</dd></div></dl> : <div className="empty-list" role="status">A verified EURUSD market snapshot is unavailable.</div>}<div className="panel-foot"><Link href="/world-state">World State</Link><Link href="/markets/EURUSD">Market detail</Link></div></section>
    </div>
  </div>;
}

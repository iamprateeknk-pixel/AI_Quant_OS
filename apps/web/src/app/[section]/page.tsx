import { notFound } from "next/navigation";
import { getAudit, getBrokers, getOrders, getSystemStatus } from "@/lib/control-api";
import { sectionDetails, type SectionKey } from "@/lib/navigation";

function isSection(value: string): value is SectionKey {
  return Object.prototype.hasOwnProperty.call(sectionDetails, value);
}

export default async function SectionPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  if (!isSection(section)) notFound();
  const [title, description] = sectionDetails[section];
  const status = section === "risk" ? await getSystemStatus() : null;
  const orders = section === "orders" ? await getOrders() : null;
  const audit = section === "audit" ? await getAudit() : null;
  const brokers = section === "brokers" ? await getBrokers() : null;
  return <div className="page-content">
    <div className="page-heading"><div><p className="eyebrow">Mission Control</p><h1>{title}</h1><p>{description}</p></div><span className="mode-tag">PAPER ENVIRONMENT</span></div>
    {section === "risk" && <section className="panel content-panel"><div className="panel-heading"><h2>Risk state</h2><span>BACKEND AUTHORITY</span></div><dl className="fact-list"><div><dt>Safety state</dt><dd>{status?.ok ? status.value.safety_state : "Unavailable"}</dd></div><div><dt>Mode</dt><dd>{status?.ok ? status.value.mode : "Unavailable"}</dd></div><div><dt>New live orders</dt><dd>Disabled</dd></div></dl><p className="panel-muted">Risk ceilings and recovery are not editable in this interface.</p></section>}
    {section === "orders" && <section className="panel content-panel"><div className="panel-heading"><h2>Paper order receipts</h2><span>{orders?.ok ? `${orders.value.orders.length} RECORDS` : "UNAVAILABLE"}</span></div>{orders?.ok && orders.value.orders.length ? <div className="table-scroll"><table><thead><tr><th>Order ID</th><th>Intent ID</th><th>Risk ID</th><th>Route</th><th>Status</th></tr></thead><tbody>{orders.value.orders.map((order) => <tr key={order.order_id}><td className="mono">{order.order_id}</td><td>{order.intent_id}</td><td className="mono">{order.risk_id}</td><td>{order.broker_route}</td><td>{order.status}</td></tr>)}</tbody></table></div> : <div className="empty-list">{orders?.ok ? "No paper orders recorded." : "Control API unavailable."}</div>}</section>}
    {section === "audit" && <section className="panel content-panel"><div className="panel-heading"><h2>Local audit events</h2><span>{audit?.ok ? `${audit.value.events.length} EVENTS` : "UNAVAILABLE"}</span></div>{audit?.ok && audit.value.events.length ? <div className="table-scroll"><table><thead><tr><th>Action</th><th>Intent</th><th>Reason</th><th>Trace</th></tr></thead><tbody>{audit.value.events.map((event, index) => <tr key={`${event.action}-${index}`}><td>{event.action}</td><td>{event.intent_id || "—"}</td><td>{event.reason}</td><td className="mono">{event.trace_id ?? "—"}</td></tr>)}</tbody></table></div> : <div className="empty-list">{audit?.ok ? "No local audit events recorded." : "Control API unavailable."}</div>}</section>}
    {section === "brokers" && <section className="panel content-panel"><div className="panel-heading"><h2>Configured routes</h2><span>{brokers?.ok ? `${brokers.value.brokers.length} ROUTE` : "UNAVAILABLE"}</span></div>{brokers?.ok && brokers.value.brokers.length ? <div className="table-scroll"><table><thead><tr><th>Broker</th><th>Connection</th><th>New orders</th><th>Instruments</th></tr></thead><tbody>{brokers.value.brokers.map((broker) => <tr key={broker.broker_id}><td>{broker.broker_id}</td><td>{broker.connection_state}</td><td>{broker.enabled ? "Enabled" : "Disabled"}</td><td>{broker.instruments.join(", ")}</td></tr>)}</tbody></table></div> : <div className="empty-list">{brokers?.ok ? "No broker routes configured." : "Control API unavailable."}</div>}</section>}
    {!status && !orders && !audit && !brokers && <section className="panel content-panel"><div className="panel-heading"><h2>Service state</h2><span>NOT CONNECTED</span></div><div className="empty-service"><span className="empty-service-mark">—</span><strong>No authoritative data source connected</strong><p>This section will populate from its phase service after contract and release-gate validation.</p></div></section>}
  </div>;
}

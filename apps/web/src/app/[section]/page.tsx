import { notFound } from "next/navigation";
import { getAudit, getBrokers, getFills, getOrders, getRisk, getSystemStatus } from "@/lib/control-api";
import { sectionDetails, type SectionKey } from "@/lib/navigation";
import { WorldStatePanel } from "@/features/world-state/world-state-panel";
import { AuditIntegrityPanel } from "@/features/audit/audit-integrity-panel";
import { OperatorConsole } from "@/features/operator-session/operator-console";
import { readBrokerCapabilities, readBrokerHealth, readDemoSyncHealth, readDemoPortfolio, readSessions, readCallBudget, readReviewSpending, readSharedSpending, readDossiers, readGenomes, readHealth, readStrategies, readMonitoringAlerts, sessionStatus } from "@/features/operator-session/server";
import { publicSession } from "@/features/operator-session/boundary";
import { InstrumentPositionsPanel } from "@/features/portfolio/instrument-positions-panel";
import { CurrencyExposurePanel } from "@/features/portfolio/currency-exposure-panel";
import { DemoPortfolioPanel } from "@/features/portfolio/demo-portfolio-panel";
import { AgentRegistryPanel } from "@/features/agents/registry-panel";
import { BrokerCapabilityPanel } from "@/features/brokers/capability-panel";
import { BrokerHealthPanel } from "@/features/brokers/health-panel";
import { DemoSyncPanel } from "@/features/brokers/demo-sync-panel";
import { PaperReconciliationPanel } from "@/features/orders/reconciliation-panel";
import { HealthPanel } from "@/features/health/health-panel";
import { GenomePanel } from "@/features/evolution/genome-panel";
import { DossierPanel } from "@/features/research/dossier-panel";
import { StrategyArchivePanel } from "@/features/strategies/archive-panel";
import { SessionAdminPanel } from "@/features/operator-session/session-admin-panel";
import { CallBudgetPanel } from "@/features/costs/call-budget-panel";
import { SpendingPanels } from "@/features/costs/shared-spending-panel";
import {MonitoringLivePanel} from "@/features/incidents/monitoring-live-panel";
import {selectMonitoringRead} from "@/features/incidents/monitoring-alerts";

function isSection(value: string): value is SectionKey {
  return Object.prototype.hasOwnProperty.call(sectionDetails, value);
}

export default async function SectionPage({ params, searchParams }: { params: Promise<{ section: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { section } = await params;
  if (!isSection(section)) notFound();
  const [title, description] = sectionDetails[section];
  const status = section === "risk" ? await getSystemStatus() : null;
  const risk = section === "risk" ? await getRisk() : null;
  const operatorResponse = section === "risk" ? await sessionStatus() : null;
  const operatorSession = operatorResponse?.ok ? publicSession(await operatorResponse.json()) : null;
  const orders = section === "orders" ? await getOrders() : null;
  const fills = section === "orders" ? await getFills() : null;
  const audit = section === "audit" ? await getAudit() : null;
  const brokerReads = section === "brokers" ? await Promise.all([getBrokers(), readDemoSyncHealth(), readBrokerCapabilities(), readBrokerHealth()]) : null;
  const brokers = brokerReads?.[0] ?? null;
  const query = section === "incidents" || section === "research" || section === "evolution" || section === "strategies" ? await searchParams : {};
  const offset = query.offset === undefined ? 0 : typeof query.offset === "string" && /^\d{1,4}$/.test(query.offset) ? Number(query.offset) : -1;
  const head = query.head === undefined ? undefined : typeof query.head === "string" ? query.head : "invalid";
  const health = section === "incidents" ? await Promise.all([readHealth("status"), readHealth("incidents", offset, head), readMonitoringAlerts()]) : null;
  const dossiers = section === "research" ? await readDossiers(offset, head) : null;
  const genomes = section === "evolution" ? await readGenomes(offset, head) : null;
  const strategies = section === "strategies" ? await readStrategies(offset, head) : null;
  const costReads = section === "costs" ? await Promise.all([readCallBudget(),readReviewSpending(),readSharedSpending()]) : null;
  const sessions = section === "settings" ? await readSessions() : null;
  const demoPortfolio = section === "portfolio" ? await readDemoPortfolio() : null;
  return <div className="page-content">
    <div className="page-heading"><div><p className="eyebrow">Mission Control</p><h1>{title}</h1><p>{description}</p></div><span className="mode-tag">PAPER ENVIRONMENT</span></div>
    {section === "world-state" && <WorldStatePanel />}
    {section === "agents" && <AgentRegistryPanel />}
    {brokerReads && <BrokerCapabilityPanel result={brokerReads[2]} observedAt={new Date().toISOString()} />}
    {brokerReads && <BrokerHealthPanel result={brokerReads[3]} observedAt={new Date().toISOString()} />}
    {brokerReads && <DemoSyncPanel result={brokerReads[1]} observedAt={new Date().toISOString()} />}
    {health && <HealthPanel statusResult={health[0]} timelineResult={health[1]} observedAt={new Date().toISOString()} />}
    {health && <MonitoringLivePanel initialResult={selectMonitoringRead(health[2])} observedAt={new Date().toISOString()} />}
    {genomes && <GenomePanel result={genomes} />}
    {strategies && <StrategyArchivePanel result={strategies} />}
    {costReads && <SpendingPanels reviewResult={costReads[1]} sharedResult={costReads[2]} />}
    {costReads && <CallBudgetPanel result={costReads[0]} />}
    {sessions && <SessionAdminPanel result={sessions} />}
    {dossiers && <DossierPanel result={dossiers} />}
    {section === "risk" && <OperatorConsole initialSession={operatorSession} safetyState={risk?.ok ? risk.value.safety_state : null} />}
    {section === "orders" && <PaperReconciliationPanel />}
    {section === "risk" && <section className="panel content-panel"><div className="panel-heading"><h2>Risk state</h2><span>BACKEND AUTHORITY</span></div><dl className="fact-list"><div><dt>Safety state</dt><dd>{risk?.ok ? risk.value.safety_state : "Unavailable"}</dd></div><div><dt>Mode</dt><dd>{status?.ok ? status.value.mode : "Unavailable"}</dd></div><div><dt>Per order ceiling, base units</dt><dd>{risk?.ok ? risk.value.max_order_exposure : "Unavailable"}</dd></div><div><dt>EURUSD position ceiling, base units</dt><dd>{risk?.ok ? risk.value.max_portfolio_exposure : "Unavailable"}</dd></div><div><dt>EURUSD worst case quantity</dt><dd>{risk?.ok ? risk.value.worst_case_quantity : "Unavailable"}</dd></div><div><dt>EURUSD filled net quantity</dt><dd>{risk?.ok ? risk.value.filled_net_quantity : "Unavailable"}</dd></div><div><dt>EURUSD pending buy / sell</dt><dd>{risk?.ok ? `${risk.value.pending_buy_quantity} / ${risk.value.pending_sell_quantity}` : "Unavailable"}</dd></div><div><dt>Per currency ceiling, {risk?.ok ? risk.value.reporting_currency : "reporting currency"}</dt><dd>{risk?.ok ? risk.value.max_currency_exposure_reporting : "Unavailable"}</dd></div><div><dt>Gross currency ceiling, {risk?.ok ? risk.value.reporting_currency : "reporting currency"}</dt><dd>{risk?.ok ? risk.value.max_gross_currency_exposure_reporting : "Unavailable"}</dd></div><div><dt>New live orders</dt><dd>Disabled</dd></div></dl><p className="panel-muted">Each instrument has its own unit ceiling. The shared currency ceilings include filled balances and pending paper orders. Both currency legs count toward gross currency exposure. Risk ceilings and recovery are not editable in this interface.</p></section>}
    {section === "portfolio" && <InstrumentPositionsPanel />}
    {section === "portfolio" && <CurrencyExposurePanel />}
    {demoPortfolio && <DemoPortfolioPanel result={demoPortfolio} observedAt={new Date().toISOString()} />}
    {section === "orders" && <section className="panel content-panel"><div className="panel-heading"><h2>Paper order receipts</h2><span>{orders?.ok ? `${orders.value.orders.length} RECORDS` : "UNAVAILABLE"}</span></div>{orders?.ok && orders.value.orders.length ? <div className="table-scroll"><table><thead><tr><th>Order ID</th><th>Instrument</th><th>Intent ID</th><th>Risk ID</th><th>Route</th><th>Broker ID</th><th>Status</th></tr></thead><tbody>{orders.value.orders.map((order) => <tr key={order.order_id}><td className="mono">{order.order_id}</td><td>{order.instrument_id}</td><td>{order.intent_id}</td><td className="mono">{order.risk_id}</td><td>{order.broker_route}</td><td className="mono">{order.broker_order_id ?? "—"}</td><td>{order.status}</td></tr>)}</tbody></table></div> : <div className="empty-list">{orders?.ok ? "No paper orders recorded." : "Control API unavailable."}</div>}</section>}
    {section === "orders" && <section className="panel content-panel"><div className="panel-heading"><h2>Simulated fills</h2><span>{fills?.ok ? `${fills.value.fills.length} RECORDS` : "UNAVAILABLE"}</span></div>{fills?.ok && fills.value.fills.length ? <div className="table-scroll"><table><thead><tr><th>Fill ID</th><th>Order ID</th><th>Instrument</th><th>Side</th><th>Quantity</th><th>Price</th><th>Fee</th><th>Source</th></tr></thead><tbody>{fills.value.fills.map((fill) => <tr key={fill.fill_id}><td className="mono">{fill.fill_id}</td><td className="mono">{fill.order_id}</td><td>{fill.instrument_id}</td><td>{fill.side}</td><td>{fill.quantity}</td><td>{fill.price}</td><td>{fill.fee}</td><td>{fill.source === "PAPER_BROKER" ? "Simulator" : "Manual"}</td></tr>)}</tbody></table></div> : <div className="empty-list">{fills?.ok ? "No simulated fills recorded." : "Fill service unavailable."}</div>}</section>}
    {section === "audit" && <AuditIntegrityPanel />}
    {section === "audit" && <section className="panel content-panel"><div className="panel-heading"><h2>Local audit events</h2><span>{audit?.ok ? `${audit.value.events.length} EVENTS` : "UNAVAILABLE"}</span></div>{audit?.ok && audit.value.events.length ? <div className="table-scroll"><table><thead><tr><th>Action</th><th>Intent</th><th>Reason</th><th>Trace</th></tr></thead><tbody>{audit.value.events.map((event, index) => <tr key={`${event.action}-${index}`}><td>{event.action}</td><td>{event.intent_id || "—"}</td><td>{event.reason}</td><td className="mono">{event.trace_id ?? "—"}</td></tr>)}</tbody></table></div> : <div className="empty-list">{audit?.ok ? "No local audit events recorded." : "Control API unavailable."}</div>}</section>}
    {section === "brokers" && <section className="panel content-panel"><div className="panel-heading"><h2>Configured routes</h2><span>{brokers?.ok ? `${brokers.value.brokers.length} ROUTE` : "UNAVAILABLE"}</span></div>{brokers?.ok && brokers.value.brokers.length ? <div className="table-scroll"><table><thead><tr><th>Broker</th><th>Connection</th><th>New orders</th><th>Instruments</th></tr></thead><tbody>{brokers.value.brokers.map((broker) => <tr key={broker.broker_id}><td>{broker.broker_id}</td><td>{broker.connection_state}</td><td>{broker.enabled ? "Enabled" : "Disabled"}</td><td>{broker.instruments.join(", ")}</td></tr>)}</tbody></table></div> : <div className="empty-list">{brokers?.ok ? "No broker routes configured." : "Control API unavailable."}</div>}</section>}
    {!status && !orders && section !== "portfolio" && section !== "world-state" && section !== "agents" && section !== "incidents" && section !== "research" && section !== "evolution" && section !== "strategies" && section !== "costs" && section !== "settings" && !audit && !brokers && <section className="panel content-panel"><div className="panel-heading"><h2>Service state</h2><span>NOT CONNECTED</span></div><div className="empty-service"><span className="empty-service-mark">—</span><strong>No authoritative data source connected</strong><p>This section will populate from its phase service after contract and release-gate validation.</p></div></section>}
  </div>;
}

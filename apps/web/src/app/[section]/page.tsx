import { notFound } from "next/navigation";
import { getAudit, getBrokers, getCurrencyPortfolio, getFills, getOrders, getPositions, getRisk, getSystemStatus, read } from "@/lib/control-api";
import { sectionDetails, type SectionKey } from "@/lib/navigation";
import { WorldStatePanel } from "@/features/world-state/world-state-panel";
import { AuditIntegrityPanel } from "@/features/audit/audit-integrity-panel";
import { OperatorConsole } from "@/features/operator-session/operator-console";
import { readBrokerCapabilities, readBrokerHealth, readDemoSyncHealth, readDemoPortfolio, readSessions, readCallBudget, readReviewSpending, readSharedSpending, readDossiers, readGenomes, readHealth, readStrategies, readMonitoringAlerts, sessionStatus } from "@/features/operator-session/server";
import { readAgentOperationalQuality } from "@/features/agents/server";
import { publicSession } from "@/features/operator-session/boundary";
import { PortfolioLivePanel } from "@/features/portfolio/portfolio-live-panel";
import { DemoPortfolioPanel } from "@/features/portfolio/demo-portfolio-panel";
import { AgentsLivePanel } from "@/features/agents/agents-live-panel";
import { AgentOperationalQualityPanel } from "@/features/agents/operational-quality-panel";
import { parseRegistry } from "@/features/agents/registry";
import { BrokerCapabilityPanel } from "@/features/brokers/capability-panel";
import { BrokerHealthPanel } from "@/features/brokers/health-panel";
import { BrokerRouteControlPanel } from "@/features/brokers/route-control-panel";
import { TableScrollRegion } from "@/features/shared/table-scroll-region";
import { DemoSyncPanel } from "@/features/brokers/demo-sync-panel";
import { PaperReconciliationPanel } from "@/features/orders/reconciliation-panel";
import { OrdersLivePanel } from "@/features/orders/orders-live-panel";
import { HealthPanel } from "@/features/health/health-panel";
import { GenomePanel } from "@/features/evolution/genome-panel";
import { DossierPanel } from "@/features/research/dossier-panel";
import { StrategyArchivePanel } from "@/features/strategies/archive-panel";
import { SessionAdminPanel } from "@/features/operator-session/session-admin-panel";
import {CostsLivePanel} from "@/features/costs/costs-live-panel";
import {MonitoringLivePanel} from "@/features/incidents/monitoring-live-panel";
import {selectMonitoringRead} from "@/features/incidents/monitoring-alerts";
import {RISK_INSTRUMENTS} from "@/features/risk/read-model";

function isSection(value: string): value is SectionKey {
  return Object.prototype.hasOwnProperty.call(sectionDetails, value);
}

export default async function SectionPage({ params, searchParams }: { params: Promise<{ section: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { section } = await params;
  if (!isSection(section)) notFound();
  const [title, description] = sectionDetails[section];
  const query = section === "risk" || section === "incidents" || section === "research" || section === "evolution" || section === "strategies" ? await searchParams : {};
  const requestedInstrument = query.instrument;
  const riskInstrument = typeof requestedInstrument === "string" && RISK_INSTRUMENTS.includes(requestedInstrument as typeof RISK_INSTRUMENTS[number]) ? requestedInstrument as typeof RISK_INSTRUMENTS[number] : "EURUSD";
  const status = section === "risk" ? await getSystemStatus() : null;
  const risk = section === "risk" ? await getRisk(riskInstrument) : null;
  const operatorResponse = section === "risk" || section === "brokers" ? await sessionStatus() : null;
  const operatorSession = operatorResponse?.ok ? publicSession(await operatorResponse.json()) : null;
  const orderReads = section === "orders" ? await Promise.all([getOrders(), getFills()]) : null;
  const agentRead = section === "agents" ? await read("/api/v1/agents/registry", parseRegistry) : null;
  const agentQualityRead = section === "agents" ? await readAgentOperationalQuality() : null;
  const audit = section === "audit" ? await getAudit() : null;
  const brokerReads = section === "brokers" ? await Promise.all([getBrokers(), readDemoSyncHealth(), readBrokerCapabilities(), readBrokerHealth()]) : null;
  const brokers = brokerReads?.[0] ?? null;
  const offset = query.offset === undefined ? 0 : typeof query.offset === "string" && /^\d{1,4}$/.test(query.offset) ? Number(query.offset) : -1;
  const head = query.head === undefined ? undefined : typeof query.head === "string" ? query.head : "invalid";
  const health = section === "incidents" ? await Promise.all([readHealth("status"), readHealth("incidents", offset, head), readMonitoringAlerts()]) : null;
  const dossiers = section === "research" ? await readDossiers(offset, head) : null;
  const genomes = section === "evolution" ? await readGenomes(offset, head) : null;
  const strategies = section === "strategies" ? await readStrategies(offset, head) : null;
  const costReads = section === "costs" ? await Promise.all([readCallBudget(),readReviewSpending(),readSharedSpending()]) : null;
  const sessions = section === "settings" ? await readSessions() : null;
  const demoPortfolio = section === "portfolio" ? await readDemoPortfolio() : null;
  const portfolioReads = section === "portfolio" ? await Promise.all([getPositions(), getCurrencyPortfolio()]) : null;
  return <div className="page-content">
    <div className="page-heading"><div><p className="eyebrow">Mission Control</p><h1>{title}</h1><p>{description}</p></div><span className="mode-tag">PAPER ENVIRONMENT</span></div>
    {section === "world-state" && <WorldStatePanel />}
    {agentRead && <AgentsLivePanel initialRead={agentRead} />}
    {agentQualityRead && <AgentOperationalQualityPanel result={agentQualityRead} />}
    {brokerReads && <BrokerCapabilityPanel result={brokerReads[2]} observedAt={new Date().toISOString()} />}
    {brokers?.ok && <BrokerRouteControlPanel enabled={brokers.value.brokers.find((broker) => broker.broker_id === "PAPER")?.enabled === true} initialSession={operatorSession} />}
    {brokerReads && <BrokerHealthPanel result={brokerReads[3]} observedAt={new Date().toISOString()} />}
    {brokerReads && <DemoSyncPanel result={brokerReads[1]} observedAt={new Date().toISOString()} />}
    {health && <HealthPanel statusResult={health[0]} timelineResult={health[1]} observedAt={new Date().toISOString()} />}
    {health && <MonitoringLivePanel initialResult={selectMonitoringRead(health[2])} observedAt={new Date().toISOString()} />}
    {genomes && <GenomePanel result={genomes} />}
    {strategies && <StrategyArchivePanel result={strategies} />}
    {costReads && <CostsLivePanel initialReads={costReads} />}
    {sessions && <SessionAdminPanel result={sessions} />}
    {dossiers && <DossierPanel result={dossiers} />}
    {section === "risk" && <OperatorConsole initialSession={operatorSession} safetyState={risk?.ok ? risk.value.safety_state : null} />}
    {section === "orders" && <PaperReconciliationPanel />}
    {section === "risk" && <section className="panel content-panel"><div className="panel-heading"><h2>Risk state</h2><span>BACKEND AUTHORITY</span></div><form method="get" action="/risk" className="filter-form"><label htmlFor="risk-instrument">Instrument</label><select id="risk-instrument" name="instrument" defaultValue={riskInstrument}>{RISK_INSTRUMENTS.map(instrument=><option key={instrument} value={instrument}>{instrument}</option>)}</select><button type="submit">View risk</button></form><dl className="fact-list"><div><dt>Safety state</dt><dd>{risk?.ok ? risk.value.safety_state : "Unavailable"}</dd></div><div><dt>Mode</dt><dd>{status?.ok ? status.value.mode : "Unavailable"}</dd></div><div><dt>Per order ceiling, base units</dt><dd>{risk?.ok ? risk.value.max_order_exposure : "Unavailable"}</dd></div><div><dt>{riskInstrument} position ceiling, base units</dt><dd>{risk?.ok ? risk.value.max_portfolio_exposure : "Unavailable"}</dd></div><div><dt>{riskInstrument} worst case quantity</dt><dd>{risk?.ok ? risk.value.worst_case_quantity : "Unavailable"}</dd></div><div><dt>{riskInstrument} filled net quantity</dt><dd>{risk?.ok ? risk.value.filled_net_quantity : "Unavailable"}</dd></div><div><dt>{riskInstrument} pending buy / sell</dt><dd>{risk?.ok ? `${risk.value.pending_buy_quantity} / ${risk.value.pending_sell_quantity}` : "Unavailable"}</dd></div><div><dt>Per currency ceiling, {risk?.ok ? risk.value.reporting_currency : "reporting currency"}</dt><dd>{risk?.ok ? risk.value.max_currency_exposure_reporting : "Unavailable"}</dd></div><div><dt>Gross currency ceiling, {risk?.ok ? risk.value.reporting_currency : "reporting currency"}</dt><dd>{risk?.ok ? risk.value.max_gross_currency_exposure_reporting : "Unavailable"}</dd></div><div><dt>New live orders</dt><dd>Disabled</dd></div></dl><p className="panel-muted">Each instrument has its own unit ceiling. The shared currency ceilings include filled balances and pending paper orders. Both currency legs count toward gross currency exposure. Risk ceilings and recovery are not editable in this interface.</p></section>}
    {portfolioReads && <PortfolioLivePanel initialReads={portfolioReads} />}
    {demoPortfolio && <DemoPortfolioPanel result={demoPortfolio} observedAt={new Date().toISOString()} />}
    {orderReads && <OrdersLivePanel initialReads={orderReads} />}
    {section === "audit" && <AuditIntegrityPanel />}
    {section === "audit" && <section className="panel content-panel"><div className="panel-heading"><h2>Local audit events</h2><span>{audit?.ok ? `${audit.value.events.length} EVENTS` : "UNAVAILABLE"}</span></div>{audit?.ok && audit.value.events.length ? <div className="table-scroll"><table><thead><tr><th>Action</th><th>Intent</th><th>Reason</th><th>Trace</th></tr></thead><tbody>{audit.value.events.map((event, index) => <tr key={`${event.action}-${index}`}><td>{event.action}</td><td>{event.intent_id || "—"}</td><td>{event.reason}</td><td className="mono">{event.trace_id ?? "—"}</td></tr>)}</tbody></table></div> : <div className="empty-list">{audit?.ok ? "No local audit events recorded." : "Control API unavailable."}</div>}</section>}
    {section === "brokers" && <section className="panel content-panel"><div className="panel-heading"><h2>Configured routes</h2><span>{brokers?.ok ? `${brokers.value.brokers.length} ROUTE` : "UNAVAILABLE"}</span></div>{brokers?.ok && brokers.value.brokers.length ? <TableScrollRegion label="Configured broker routes table"><table><thead><tr><th>Broker</th><th>Connection</th><th>Adapter enabled</th><th>Instruments</th></tr></thead><tbody>{brokers.value.brokers.map((broker) => <tr key={broker.broker_id}><td>{broker.broker_id}</td><td>{broker.connection_state}</td><td>{broker.enabled ? "Enabled" : "Disabled"}</td><td>{broker.instruments.join(", ")}</td></tr>)}</tbody></table></TableScrollRegion> : <div className="empty-list">{brokers?.ok ? "No broker routes configured." : "Control API unavailable."}</div>}</section>}
    {!status && !orderReads && section !== "portfolio" && section !== "world-state" && section !== "agents" && section !== "incidents" && section !== "research" && section !== "evolution" && section !== "strategies" && section !== "costs" && section !== "settings" && !audit && !brokers && <section className="panel content-panel"><div className="panel-heading"><h2>Service state</h2><span>NOT CONNECTED</span></div><div className="empty-service"><span className="empty-service-mark">—</span><strong>No authoritative data source connected</strong><p>This section will populate from its phase service after contract and release-gate validation.</p></div></section>}
  </div>;
}

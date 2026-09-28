export const sections = [
  { href: "/", label: "Mission Control", group: "Overview" },
  { href: "/world-state", label: "World State", group: "Intelligence" },
  { href: "/markets/EURUSD", label: "Markets", group: "Intelligence" },
  { href: "/brokers", label: "Brokers", group: "Trading" },
  { href: "/strategies", label: "Strategies", group: "Trading" },
  { href: "/agents", label: "Agents", group: "Trading" },
  { href: "/risk", label: "Risk", group: "Trading" },
  { href: "/portfolio", label: "Portfolio", group: "Trading" },
  { href: "/orders", label: "Orders", group: "Trading" },
  { href: "/research", label: "Research", group: "Development" },
  { href: "/evolution", label: "Evolution", group: "Development" },
  { href: "/incidents", label: "Incidents", group: "Operations" },
  { href: "/traces/demo", label: "Traces", group: "Operations" },
  { href: "/costs", label: "Costs", group: "Operations" },
  { href: "/audit", label: "Audit", group: "Operations" },
  { href: "/settings", label: "Settings", group: "Operations" },
] as const;

export const sectionDetails = {
  "world-state": ["World State", "Regime, currency twins, macro context, and evidence freshness will appear when the world-state service is connected."],
  brokers: ["Brokers", "The paper simulator is the only enabled route. Exness remains a planned Priority-1 adapter subject to eligibility and release gates."],
  strategies: ["Strategies", "No strategy is active. Validated candidates and allocation history will appear here."],
  agents: ["Agents", "The 18-agent runtime has not been connected. No agent has capital authority."],
  risk: ["Risk Center", "Deterministic safety state and paper order and portfolio ceilings are shown below."],
  portfolio: ["Paper Portfolio", "Filled EURUSD units and quote-currency trading P&L from the backend. No account equity is inferred."],
  orders: ["Orders", "Paper order receipts and manually reported simulated fills. Broker reconciliation is not connected."],
  research: ["Research", "Experiments and validation dossiers will appear after the research pipeline is connected."],
  evolution: ["Evolution", "Champion and challenger promotions require offline, replay, shadow, and canary evidence."],
  incidents: ["Incidents", "Incident timeline and controlled recovery will appear after observability integration."],
  costs: ["Costs", "Model, compute, and data costs are not being collected yet."],
  audit: ["Audit", "Paper control events persist when the local SQLite database is configured. Tamper-evident audit remains a release gate."],
  settings: ["Settings", "Only non-capital preferences will be managed here. Live enablement is not a UI toggle."],
} as const;

export type SectionKey = keyof typeof sectionDetails;

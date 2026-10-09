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
  { href: "/traces", label: "Traces", group: "Operations" },
  { href: "/costs", label: "Costs", group: "Operations" },
  { href: "/audit", label: "Audit", group: "Operations" },
  { href: "/settings", label: "Settings", group: "Operations" },
] as const;

export const sectionDetails = {
  "world-state": ["World State", "Versioned paper quote and regime evidence, currency coverage, and source freshness. Macro sources remain unconnected."],
  brokers: ["Brokers", "The paper simulator is the only enabled route. Exness remains a planned Priority-1 adapter subject to eligibility and release gates."],
  strategies: ["Strategies", "Saved implementation versions and source fingerprints. Approval, operating lifecycle and allocation history remain pending."],
  agents: ["Agents", "Versioned roles, development coverage and lifecycle restrictions from the paper agent registry. No agent has capital authority."],
  risk: ["Risk Center", "Deterministic safety state and paper order and portfolio ceilings are shown below."],
  portfolio: ["Portfolio", "Paper positions and currency exposure, followed by a protected saved demo account observation. Demo amounts carry no trading authority."],
  orders: ["Orders", "Paper order receipts, manual fills, and fills imported from the durable simulator. Uncertain orders require operator reconciliation."],
  research: ["Research", "Verified archived candidate outcomes, validation gates and provenance. Research evidence does not authorize promotion."],
  evolution: ["Evolution", "Saved agent genome lineage and evaluation objectives. Challengers remain unverified; promotion requires all release gates."],
  incidents: ["Incidents", "Verified paper health incidents, monitoring observations and retained quarantine restrictions. Recovery remains a separately gated workflow."],
  costs: ["Costs", "Protected agent call capacity and recovery reserves. Model, compute, and data charges remain unmeasured."],
  audit: ["Audit", "Paper control events persist with a verified local hash chain. Independent immutable storage remains a release gate."],
  settings: ["Settings", "Manage local owner sessions. Display preferences and integration administration remain pending."],
} as const;

export type SectionKey = keyof typeof sectionDetails;

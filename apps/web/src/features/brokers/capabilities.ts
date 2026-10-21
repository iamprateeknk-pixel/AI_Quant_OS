export type BrokerCapabilityView = {
  broker_id: string; profile_version: "0.1.0";
  implementation: string; operations: string[]; instruments: string[];
  bound_to_execution_router: boolean; eligible: boolean; compliance_scope: string;
  paper_admission_policy_allows: boolean; native_acceptance: string;
};
const profiles = {
  PAPER: {implementation:"PAPER_SIMULATOR",source:"ai_quant_os.broker",operations:["SIMULATED_ORDER_PLACE","SIMULATED_ORDER_LOOKUP","SIMULATED_FILLS_READ","SIMULATED_FILL_REPORT"],acceptance:"NOT_APPLICABLE"},
  EXNESS_MT5_DEMO: {implementation:"ISOLATED_DEMO_ORDER_CLI",source:"ai_quant_os.exness_demo_order_cli",operations:["QUOTE_READ","ACCOUNT_READ","POSITIONS_READ","ACTIVE_ORDERS_READ","DEALS_READ","DEMO_ORDER_SUBMIT_EXPLICIT","DEMO_ORDER_RECONCILE_READ_ONLY"],acceptance:"UNVERIFIED"},
  EXNESS: {implementation:"DISABLED_SKELETON",source:"ai_quant_os.broker_skeletons",operations:[],acceptance:"UNVERIFIED"},
  IBKR: {implementation:"DISABLED_SKELETON",source:"ai_quant_os.broker_skeletons",operations:[],acceptance:"UNVERIFIED"},
  ZERODHA: {implementation:"DISABLED_SKELETON",source:"ai_quant_os.broker_skeletons",operations:[],acceptance:"UNVERIFIED"},
};
function object(value: unknown): value is Record<string,unknown> {return typeof value === "object" && value !== null && !Array.isArray(value);}
export function parseBrokerCapabilities(value: unknown): BrokerCapabilityView[] | null {
  if (!object(value) || value.schema_version !== "1.0" || value.registry_kind !== "DECLARED_LOCAL_IMPLEMENTATIONS" || value.live_execution_released !== false || value.may_affect_portfolio !== false || !Array.isArray(value.brokers) || value.brokers.length !== 5) return null;
  const rows: BrokerCapabilityView[] = [], seen = new Set<string>();
  for (const item of value.brokers) {
    if (!object(item) || typeof item.broker_id !== "string" || !Object.prototype.hasOwnProperty.call(profiles,item.broker_id) || seen.has(item.broker_id)) return null;
    const profile = profiles[item.broker_id as keyof typeof profiles];
    if (item.profile_version !== "0.1.0" || item.implementation !== profile.implementation || item.source_module !== profile.source || item.native_acceptance !== profile.acceptance || item.live_execution_released !== false) return null;
    if (typeof item.bound_to_execution_router !== "boolean" || typeof item.eligible !== "boolean" || typeof item.paper_admission_policy_allows !== "boolean" || typeof item.compliance_scope !== "string" || !["LOCAL_SIMULATION_ONLY","UNVERIFIED","DENIED"].includes(item.compliance_scope)) return null;
    if (!Array.isArray(item.operations) || item.operations.length !== profile.operations.length || item.operations.some(operation=>typeof operation !== "string") || [...item.operations].sort().join(",") !== [...profile.operations].sort().join(",")) return null;
    if (!Array.isArray(item.instruments) || item.instruments.length > 4 || item.instruments.some(instrument=>typeof instrument !== "string" || !["EURUSD","GBPUSD","USDJPY","EURJPY"].includes(instrument)) || new Set(item.instruments).size !== item.instruments.length || (item.broker_id !== "PAPER" && item.instruments.length !== 0)) return null;
    if (item.paper_admission_policy_allows && (item.broker_id !== "PAPER" || !item.eligible || !item.bound_to_execution_router || item.compliance_scope !== "LOCAL_SIMULATION_ONLY")) return null;
    seen.add(item.broker_id);
    rows.push({broker_id:item.broker_id,profile_version:"0.1.0",implementation:profile.implementation,operations:[...profile.operations],instruments:[...item.instruments].sort(),bound_to_execution_router:item.bound_to_execution_router,eligible:item.eligible,compliance_scope:item.compliance_scope,paper_admission_policy_allows:item.paper_admission_policy_allows,native_acceptance:profile.acceptance});
  }
  const order = Object.keys(profiles);
  return rows.sort((a,b)=>order.indexOf(a.broker_id)-order.indexOf(b.broker_id));
}

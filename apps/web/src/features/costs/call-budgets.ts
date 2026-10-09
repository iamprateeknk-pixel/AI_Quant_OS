export type AgentCallCounters = {
  agent_id: string;
  daily_calls: number;
  monthly_calls: number;
  daily_remaining: number;
  monthly_remaining: number;
  daily_recovery_reserve: number;
  monthly_recovery_reserve: number;
};

export type CallBudget = {
  schema_version: "1.0";
  mode: "PAPER";
  state: "ENFORCED" | "DISABLED" | "UNAVAILABLE";
  accounting_basis: "RESERVED_ROUTINE_TOOL_CALL_ATTEMPTS";
  recovery_spending_enabled: false;
  provider_token_accounting: false;
  billable_cost_accounting: false;
  day: string | null;
  month: string | null;
  head_hash: string | null;
  policy_sha256: string | null;
  agents: AgentCallCounters[];
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function counter(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 100000;
}

function parseCounters(value: unknown): AgentCallCounters | null {
  if (!record(value) || typeof value.agent_id !== "string" || !/^A(0[1-9]|1[0-8])$/.test(value.agent_id)) return null;
  const { daily_calls, monthly_calls, daily_remaining, monthly_remaining, daily_recovery_reserve, monthly_recovery_reserve } = value;
  if (!counter(daily_calls) || !counter(monthly_calls) || !counter(daily_remaining) || !counter(monthly_remaining) || !counter(daily_recovery_reserve) || !counter(monthly_recovery_reserve)) return null;
  const dailyCeiling = daily_calls + daily_remaining + daily_recovery_reserve;
  const monthlyCeiling = monthly_calls + monthly_remaining + monthly_recovery_reserve;
  if (daily_calls > monthly_calls || daily_recovery_reserve > monthly_recovery_reserve || dailyCeiling > monthlyCeiling || monthlyCeiling > 100000) return null;
  return { agent_id: value.agent_id, daily_calls, monthly_calls, daily_remaining, monthly_remaining, daily_recovery_reserve, monthly_recovery_reserve };
}

export function parseCallBudget(value: unknown): CallBudget | null {
  if (!record(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || value.accounting_basis !== "RESERVED_ROUTINE_TOOL_CALL_ATTEMPTS" || value.recovery_spending_enabled !== false || value.provider_token_accounting !== false || value.billable_cost_accounting !== false || !Array.isArray(value.agents)) return null;
  const state = value.state;
  if (state !== "ENFORCED" && state !== "DISABLED" && state !== "UNAVAILABLE") return null;
  const base = { schema_version: "1.0", mode: "PAPER", state, accounting_basis: "RESERVED_ROUTINE_TOOL_CALL_ATTEMPTS", recovery_spending_enabled: false, provider_token_accounting: false, billable_cost_accounting: false } as const;
  if (state !== "ENFORCED") {
    if (value.agents.length || [value.day, value.month, value.head_hash, value.policy_sha256].some(item => item !== null)) return null;
    return { ...base, day: null, month: null, head_hash: null, policy_sha256: null, agents: [] };
  }
  if (typeof value.day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value.day) || typeof value.month !== "string" || value.day.slice(0, 7) !== value.month) return null;
  const date = new Date(`${value.day}T00:00:00Z`);
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value.day) return null;
  if (typeof value.head_hash !== "string" || !/^[a-f0-9]{64}$/.test(value.head_hash) || typeof value.policy_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.policy_sha256) || value.agents.length !== 18) return null;
  const agents: AgentCallCounters[] = [];
  for (const input of value.agents) {
    const row = parseCounters(input);
    if (!row || agents.some(agent => agent.agent_id === row.agent_id)) return null;
    agents.push(row);
  }
  return { ...base, day: value.day, month: value.month, head_hash: value.head_hash, policy_sha256: value.policy_sha256, agents: agents.sort((a, b) => a.agent_id.localeCompare(b.agent_id)) };
}

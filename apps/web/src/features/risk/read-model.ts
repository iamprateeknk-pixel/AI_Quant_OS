export type SafetyState = "NORMAL" | "NO_NEW_RISK" | "FLATTEN_ONLY" | "HALT";
export type SystemStatus = { mode: "PAPER"; safety_state: SafetyState; order_count: number };
export type RiskView = {
  instrument_id: "EURUSD" | "GBPUSD" | "USDJPY" | "EURJPY";
  safety_state: SafetyState;
  max_order_exposure: string;
  max_portfolio_exposure: string;
  worst_case_quantity: string;
  filled_net_quantity: string;
  pending_buy_quantity: string;
  pending_sell_quantity: string;
  portfolio_snapshot_id: string;
  reporting_currency: string;
  max_currency_exposure_reporting: string;
  max_gross_currency_exposure_reporting: string;
};

const safetyStates = ["NORMAL", "NO_NEW_RISK", "FLATTEN_ONLY", "HALT"] as const;
const instruments = ["EURUSD", "GBPUSD", "USDJPY", "EURJPY"] as const;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function decimal(value: unknown, allowNegative: boolean, allowZero: boolean): value is string {
  if (typeof value !== "string" || !/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)) return false;
  const parsed = Number(value);
  return Number.isFinite(parsed) && (allowNegative || parsed >= 0) && (allowZero || parsed > 0);
}

export function parseSystemStatus(value: unknown): SystemStatus | null {
  if (!record(value) || !exactKeys(value, ["mode", "safety_state", "order_count"]) || value.mode !== "PAPER" ||
      !safetyStates.includes(value.safety_state as SafetyState) || typeof value.order_count !== "number" ||
      !Number.isSafeInteger(value.order_count) || value.order_count < 0) return null;
  return {mode:"PAPER",safety_state:value.safety_state as SafetyState,order_count:value.order_count};
}

export function parseRiskView(value: unknown, instrument: RiskView["instrument_id"] = "EURUSD"): RiskView | null {
  const keys = ["instrument_id", "safety_state", "max_order_exposure", "max_portfolio_exposure", "worst_case_quantity", "filled_net_quantity", "pending_buy_quantity", "pending_sell_quantity", "portfolio_snapshot_id", "reporting_currency", "max_currency_exposure_reporting", "max_gross_currency_exposure_reporting"];
  if (!record(value) || !exactKeys(value, keys) || value.instrument_id !== instrument || !instruments.includes(value.instrument_id as RiskView["instrument_id"]) ||
      !safetyStates.includes(value.safety_state as SafetyState) ||
      !decimal(value.max_order_exposure, false, false) || !decimal(value.max_portfolio_exposure, false, false) ||
      !decimal(value.worst_case_quantity, false, true) || !decimal(value.filled_net_quantity, true, true) ||
      !decimal(value.pending_buy_quantity, false, true) || !decimal(value.pending_sell_quantity, false, true) ||
      typeof value.portfolio_snapshot_id !== "string" || value.portfolio_snapshot_id.length < 1 ||
      typeof value.reporting_currency !== "string" || !/^[A-Z]{3}$/.test(value.reporting_currency) ||
      !decimal(value.max_currency_exposure_reporting, false, false) || !decimal(value.max_gross_currency_exposure_reporting, false, false)) return null;
  return {
    instrument_id:value.instrument_id as RiskView["instrument_id"],
    safety_state:value.safety_state as SafetyState,
    max_order_exposure:value.max_order_exposure as string,
    max_portfolio_exposure:value.max_portfolio_exposure as string,
    worst_case_quantity:value.worst_case_quantity as string,
    filled_net_quantity:value.filled_net_quantity as string,
    pending_buy_quantity:value.pending_buy_quantity as string,
    pending_sell_quantity:value.pending_sell_quantity as string,
    portfolio_snapshot_id:value.portfolio_snapshot_id as string,
    reporting_currency:value.reporting_currency,
    max_currency_exposure_reporting:value.max_currency_exposure_reporting as string,
    max_gross_currency_exposure_reporting:value.max_gross_currency_exposure_reporting as string,
  };
}

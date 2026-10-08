/** Non-secret original command checkpoint. Restoring never sends a command. */
export type Action = "NO_NEW_RISK" | "FLATTEN_ONLY" | "HALT";
export type Command = { action_id: string; requested_action: Action; reason: string; operator_session_id: string;
  client_timestamp: string; expected_current_state: string; confirmation: string };
export const PENDING_KEY = "ai_quant_pending_control_v1";

export function encodePending(value: unknown): string {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Invalid pending action");
  const input = value as Record<string, unknown>;
  const keys = ["action_id", "requested_action", "reason", "operator_session_id", "client_timestamp", "expected_current_state", "confirmation"];
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  if (Object.keys(input).length !== keys.length || Object.keys(input).some((key) => !keys.includes(key)) ||
      keys.some((key) => typeof input[key] !== "string") || !uuid.test(String(input.action_id)) || !uuid.test(String(input.operator_session_id)) ||
      !["NO_NEW_RISK", "FLATTEN_ONLY", "HALT"].includes(String(input.requested_action)) ||
      !["NORMAL", "CAUTION", "REDUCED_RISK", "NO_NEW_RISK", "FLATTEN_ONLY", "HALT"].includes(String(input.expected_current_state)) ||
      !String(input.reason).trim() || String(input.reason).length > 512 || !Number.isFinite(Date.parse(String(input.client_timestamp))) ||
      !/(Z|\+00:00)$/.test(String(input.client_timestamp)) ||
      (input.requested_action !== "NO_NEW_RISK" && input.confirmation !== input.requested_action)) throw new Error("Invalid pending action");
  const raw = JSON.stringify(Object.fromEntries(keys.map((key) => [key, input[key]])));
  if (raw.length > 4096) throw new Error("Pending action too large");
  return raw;
}

export function decodePending(raw: string): Command {
  if (raw.length > 4096) throw new Error("Pending action too large");
  const value = JSON.parse(raw);
  encodePending(value);
  return value as Command;
}

export function savePending(storage: Pick<Storage, "setItem" | "getItem">, command: Command): void {
  const encoded = encodePending(command), prior = storage.getItem(PENDING_KEY);
  if (prior !== null && encodePending(decodePending(prior)) !== encoded) throw new Error("Unresolved action cannot be replaced");
  storage.setItem(PENDING_KEY, encoded);
  if (storage.getItem(PENDING_KEY) !== encoded) throw new Error("Action checkpoint could not be retained");
}
export function clearPending(storage: Pick<Storage, "getItem" | "removeItem">, actionId: string): void {
  const raw = storage.getItem(PENDING_KEY);
  if (raw !== null && decodePending(raw).action_id !== actionId) throw new Error("Pending action identity changed");
  storage.removeItem(PENDING_KEY);
}

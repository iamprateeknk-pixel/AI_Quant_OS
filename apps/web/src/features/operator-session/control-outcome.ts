export type Outcome = { receipt_id: string; action_id: string; decision: "ACCEPTED" | "REJECTED" | "PENDING";
  rejection_reason?: string; authoritative_state: string; trace_id: string; server_timestamp: string };

export function parseOutcome(input: Record<string, unknown>, actionId: string): Outcome {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const states = ["NORMAL", "CAUTION", "REDUCED_RISK", "NO_NEW_RISK", "FLATTEN_ONLY", "HALT"];
  const base = ["PAPER_ONLY", "VALID_SESSION", "FRESH_REAUTH"];
  const checks = input.decision === "ACCEPTED" ? [...base, "EXPECTED_STATE", "MONOTONIC_SAFETY"] :
    input.decision === "PENDING" ? [...base, "RESERVED_NOT_APPLIED"] : [...base, input.rejection_reason];
  if (input.schema_version !== "1.0" || input.mode !== "PAPER" || input.action_id !== actionId ||
      !uuid.test(String(input.receipt_id)) || input.trace_id !== input.receipt_id || !uuid.test(String(input.operator_session_id)) ||
      !/^[0-9a-f]{64}$/.test(String(input.request_sha256)) || typeof input.server_timestamp !== "string" ||
      !Number.isFinite(Date.parse(input.server_timestamp)) || !states.includes(String(input.previous_state)) || !states.includes(String(input.authoritative_state)) ||
      !["ACCEPTED", "REJECTED", "PENDING"].includes(String(input.decision)) ||
      JSON.stringify(input.policy_checks) !== JSON.stringify(checks) ||
      (input.decision !== "ACCEPTED" && input.previous_state !== input.authoritative_state) ||
      (input.decision === "ACCEPTED" && !["NO_NEW_RISK", "FLATTEN_ONLY", "HALT"].includes(String(input.authoritative_state))) ||
      (input.decision === "REJECTED" && !["STALE_STATE", "STALE_COMMAND", "SAFETY_RECOVERY_FORBIDDEN"].includes(String(input.rejection_reason)))) {
    throw new Error("Backend receipt could not be verified. Keep this action identity for checking.");
  }
  return input as Outcome;
}

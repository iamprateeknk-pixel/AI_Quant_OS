# Deterministic control ownership

This map names the current code owner for each safety-critical paper action. An agent proposal, UI state, broker observation, or stale receipt cannot substitute for the named owner. Production/live release ownership is not implemented by this map. Agent tool
ceilings, scope rules, sandbox-only operations, and lifecycle restrictions are
listed in [agent-permission-matrix.json](agent-permission-matrix.json), whose
parity is checked against the runtime policy.

| Critical action | Deterministic owner | Persisted or checked evidence |
| --- | --- | --- |
| Decide whether a paper intent may add risk | `RiskKernel` (`src/ai_quant_os/risk.py`) | Current limits, safety state, quote validity, and issued decision identity |
| Accept and route a paper order; reconcile its receipt | `PaperExecutionGateway` (`src/ai_quant_os/execution.py`) with the configured `BrokerRouter` (`src/ai_quant_os/broker.py`) | Idempotent paper order receipt, owned decision, simulator acknowledgement, and reconciliation records |
| Escalate to NO_NEW_RISK, FLATTEN_ONLY, or HALT | API control route calls `RiskKernel.set_safety_state`; `PaperStateStore` records the idempotent control receipt and audit event before the state change | Authorized operator, reason, idempotency key, backend receipt, and audit-chain integrity |
| Recover paper safety | `PaperExecutionGateway.recover_paper_safety` and `RiskKernel`, with `PaperStateStore` providing the durable receipt | Distinct recovery credential, unresolved-order/fill checks, reason, idempotency key, and audit event |
| Quarantine or constrain an agent | `AgentRegistry` plus `HealthWatchdog` policy | Immutable manifest identity, server-owned lifecycle, and durable health evidence |
| Execute an admitted advisory agent graph | `GraphRuntime` with current `AgentRegistry` and typed `AgentImplementation` | Context and manifest identity, tool admission, durable run reservation, schema validation, and typed receipt |
| Enable a production/live strategy or broker route | **No implementation owner: release is disabled and blocked** | P13 signed release evidence and independent authorization are not present; this action must not be inferred from paper/shadow artifacts |

Agents can submit bounded proposals and observations. They cannot own risk limits, order acceptance, recovery, broker credentials, or live enablement. The source boundary is additionally guarded by `tests/test_architecture_broker_boundary.py` for direct agent/strategy imports; that static check is not a full indirect call-graph proof.

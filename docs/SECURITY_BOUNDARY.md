# Initial security and authority boundary

This is the authority map for the paper foundation. It is a design input for a later full threat model and security review; it does not certify a live deployment.

| Actor | May do now | Must not do |
| --- | --- | --- |
| Local operator with configured token | Submit simulated quotes and intents; escalate safety state | Route to a real broker, lower a safety state alone, change risk ceilings |
| Paper recovery holder with a distinct configured token | Join the operator credential to recover `NO_NEW_RISK` after paper order and fill checks | Recover `HALT` or `FLATTEN_ONLY`, route to a live broker |
| Unauthenticated local reader | Read paper status, risk state, orders and audit | Call control or paper write endpoints |
| Future cognitive agent | Propose a versioned `TradeIntent` through a controlled service | Supply `RiskDecision`, credentials, order command or risk policy |
| Risk kernel | Issue decisions from fixed limits and current market snapshot | Read broker credentials or self-modify its limits |
| Paper gateway | Verify kernel-issued approval and create a paper receipt | Access a broker SDK or send a live order |

## Threats and controls in this slice

- Forged approval: the gateway checks each decision against the kernel's issued-decision registry. A decision object built elsewhere cannot authorize an order.
- Replayed submission: an idempotency key returns the first paper order only when the full intent matches. A changed intent is rejected. SQLite can retain this receipt across a restart.
- Stale or untrusted market: the risk kernel rejects new risk when the quote is too old, future-dated, untrusted, or has an incompatible world-state version.
- Operator token exposure: the token is read from an environment variable and compared without early exit. The development API must bind to localhost. There is no production-grade identity, MFA, session expiry or rate limit yet.
- Process failure: quotes and decisions are in memory. With `AI_QUANT_PAPER_DB`, paper order receipts, safety state, recovery receipts and audit records survive restart. Broader recovery testing is mandatory before any shadow or live phase.
- Premature paper recovery: a separate token, idempotency key, absence of unresolved orders and matching simulated broker fill ledger gate `NO_NEW_RISK` recovery. The database records the recovery and audit event before the in-process risk state changes. Recovery discards approvals issued before the incident so new orders require fresh evaluation. This is a local paper control, not production dual approval.
- Audit history changes: a transactionally maintained hash chain detects modified, missing and untracked local events. Startup verifies history, audit reads withhold invalid history, and authenticated writes reject a failed integrity check. Legacy events are explicitly baselined at migration. This does not protect against a database administrator recomputing the whole chain or restoring an older consistent copy; independent immutable storage/checkpoints remain required.
- Concurrent safety escalation: order creation and safety-state changes share the kernel authorization lock, so a new order cannot be accepted after escalation completes in this process.

No broker credentials, broker adapter or live order endpoint are present. Production authorization, secrets management, immutable audit storage, networking, and incident recovery remain open phase gates.

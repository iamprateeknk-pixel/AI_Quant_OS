# ADR 0215: Explicit admission for isolated Exness demo orders

Status: accepted for local groundwork; native and independent acceptance pending.

The local single-operator workstation scope permits an explicitly enrolled
Exness MT5 demo order path as P04 groundwork. Keep this path outside PAPER
routing and require a separately supervised worker, DEMO account identity
checks, environment-only password delivery, bounded order sizing, and an
operator-supplied `--submit-demo-only` switch for every invocation.

Reserve each idempotency key durably before native submission and bind it to the
exact command and account scope. Never retry after an uncertain send or worker
custody outcome. Map IOC and FOK to their matching MT5 fill modes and recheck
expiry immediately before submission. Reject identifiers outside the receipt
contract before reserving them.

This decision provides neither native broker acceptance nor continuous order
reconciliation or recovery. The gateway has not connected to an account in this
increment. P04 native/service/reconciliation and independent acceptance remain
open; live execution remains disabled until P13 gates are met. Operator
instructions and limits are in `docs/EXNESS_DEMO_ORDER_HANDOFF.md`.

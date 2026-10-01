# 0033 — Owner-bound local paper recovery application

Status: Accepted for opt-in single-process localhost paper use.

## Decision

Connect the Temporal worker in the existing FastAPI lifespan, binding actual
application gateway/kernel/store instances. Recovery activities share the operator
control lock, so reconciliation and manual safety transitions cannot overlap.
The optional SDK is imported only for enabled startup. Persistent paper storage,
an operator token and a literal 127.0.0.1 target are required.

Operator-authenticated start/read/retry routes accept bounded opaque IDs and no
client verdicts. Actual paper ownership is admitted before RPC dispatch. Workflow
identities and task queues derive from the canonical paper file path. Duplicate
active/completed workflows are refused; uncertain RPC start leaves durable safety
containment in place. Retry only requests a new lookup while the workflow waits.

Manual NO_NEW_RISK normalization must additionally find review-ready durable latest
attempt evidence for every recorded triage incident. It still requires distinct
operator/recovery tokens, resolved orders and matching positions. There is no
HALT-to-NORMAL path, production repair approval or agent recovery authority.

## Verification and limits

Actual Temporal integration tests start and restart the HTTP app against the same
service and paper ledger, check protected commands/strict inputs/duplicate starts,
show broker-fill mismatch HALT in the running API's actual kernel, preserve fills
without import/resend, and refuse unavailable required service startup with durable
HALT. A regression test proves accepted orders cannot bypass unfinished triage.

Worker failure attempts audited HALT and fails application lifespan; failed disk
writes guarantee only current-process HALT. Normal shutdown drains activities,
including repeatedly cancelled callbacks whose underlying state write is still
running. Forced process/event-loop termination cannot guarantee completing I/O.
Single owner process, localhost transport and preserved database path are required.
Cross-process leases, production TLS/auth, workflow migrations, full operator
sessions, health watchdog, repair verification and shadow/canary recovery remain.

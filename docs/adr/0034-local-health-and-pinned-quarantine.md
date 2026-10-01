# 0034 — Local health watchdog and pinned quarantine

Status: Accepted for opt-in single-owner paper diagnostics.

## Decision

Observe actual paper orders/positions/quotes, owning audit integrity and verified
LangGraph receipts. A pure fixed failure taxonomy chooses NO_NEW_RISK or HALT;
agent failure additionally pins QUARANTINED to its exact manifest/version. The
watchdog shares the application's control lock. Health and recovery containment
hold kernel authority through the audit commit; concurrent approvals wait and
then reject. Domain contracts do not import
FastAPI, Pydantic or storage. No health source comes from a client verdict.

Persist first-observation evidence in a separate bounded, audited SQLite journal.
This permits recording an original paper audit failure. Restore all pins before
agent use, including when the periodic monitor is disabled. Every operator
admission verifies an existing health journal and fails closed on tampering.
Ordinary lifecycle changes cannot clear pinned quarantine. Open health incidents
block manual safety normalization; this increment has no resolution authority.

Scan at most 64 verified graph records per page, including non-failure records so
tampering cannot hide behind a status filter. Enabled startup covers the complete
bounded ledger, and normal monitoring rotates. Immediately observe terminal
execute/resume receipts. Drain thread work on normal cancellation/shutdown and clean
startup resources if a later required service fails.

## Consequences

Actual SQLite, simulator, LangGraph and HTTP checks cover restart quarantine,
permission denial, first-observation deduplication, audit-independent evidence,
rehash/deletion/capacity/serialization refusal, multi-page detection, current-kernel
and durable HALT, clock regression and repeated/late-failure cancellation.

Hash chaining is local integrity evidence, not external tamper-proof signing.
Disk failure guarantees only current-process HALT if neither persistence channel
can write. The journal caps incidents at 1,000; archival/closure is future work and
capacity never evicts an unresolved stop. Live trading, automatic order retries,
agent restart, safety downgrade and implementation promotion remain absent.
Separate candidate repair versions and independent shadow/canary approval must
precede a future quarantine release mechanism.

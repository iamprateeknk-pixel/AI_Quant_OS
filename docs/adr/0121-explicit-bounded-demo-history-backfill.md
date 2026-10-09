# 0121 - Explicit bounded history backfill after a demo sync gap

Status: implemented locally; native and independent acceptance pending.

## Decision

Add a strict BACKFILL_HISTORY request with observed revision and caller-owned
attempt UUID. From a history-gap halt with no pending work, verify custody,
identity availability, original coverage, clock and capacity. Commit the exact
oldest one-day-or-smaller window as audited ACTIVE intent before dispatching one
existing fixed-worker capture. Use existing receipt/projection completion and
restart handling; do not add a multi-day loop or silently skip coverage.

Use the canonical history-start rule for ordinary ticks, clock admission and
backfill. Share projection/cursor admission between the recovery actions.
Expose one mutually exclusive CLI action under existing enrollment/ownership
gates and original journal preflight. Public authority remains denied.

## Consequences

Operators can close long gaps incrementally with retained overlap and deal
versions. Exact intent survives interruption, and old/reused commands cannot
create a second capture. Unknown custody remains halted. Native deal bounds can
still withhold an oversized day; subdivision and completeness acceptance remain
open. Current account/quote context is not historical account state.

Prior wire schemas and tables remain unchanged. New backfill audit metadata
requires the new reader; rollback stops launchers and preserves journals.
Verification uses fixture observations only. Native, deployment, custody,
checkpoint migration, independent review and P13 acceptance remain pending.

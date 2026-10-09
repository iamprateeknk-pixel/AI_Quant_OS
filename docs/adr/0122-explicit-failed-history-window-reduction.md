# 0122 - Explicit reduction of a confirmed failed history window

Status: implemented locally; native and independent acceptance pending.

## Decision

Allow an operator to replace only a retained, confirmed-exit CAPACITY or
RESULT_LIMIT receipt with a distinct, strictly smaller window from the same
oldest start. Require exact halted revision/prior UUID, new caller UUID, coverage
progress, capacity, clock, verified gateway custody and projection agreement.
Commit the replacement intent and failed receipt commitment before one existing
fixed-worker capture. Retain the failed attempt and all audit history.

Refuse windows ending in the existing overlap, preventing cursor regression or
zero-progress imports. Refuse uncertain/missing and non-capacity receipts;
there is no reset, child adoption or automatic subdivision. Expose one mutually
exclusive CLI action under existing enrollment/ownership/original-store gates.

## Consequences

Oversized history can be reduced explicitly without skipping its beginning or
rerunning the failed native identity. Replacement intent survives interruption.
Capacity/output denial can have other causes, so success is not guaranteed or
claimed. Native history and terminal custody remain unverified; all order and
portfolio authority remains denied.

Existing public schemas/tables are unchanged. New reduction metadata requires
the current reader; rollback stops launchers and preserves journals. Native,
deployment, custody, migration, independent review and P13 acceptance remain
pending. Verification uses local native fixtures only.

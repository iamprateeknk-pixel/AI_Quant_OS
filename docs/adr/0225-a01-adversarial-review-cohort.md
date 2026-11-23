# ADR 0225: A01 adversarial review cohort

## Status

Implemented as an additive advisory cohort. Runtime and service acceptance remain
partial; no adversarial or production gate is closed by the supervisor route.

## Decision

Add immutable A01 0.10.0 with one typed SANDBOX declaration containing the task
ID and research experiment ID. The versioned registry admits only A01 0.10.0 and
A11 0.2.0 for execution. The parent plan selects at most that one A11 version,
and its deterministic child run ID binds the content-addressed parent context.

Before compiling the child, dispatch rechecks that the parent context declares
the same experiment ID. A11's existing child compiler obtains the verified
candidate, validation, stress, and HOLDOUT scope snapshot through the
service-owned RPC path. The parent receives only A11's bounded scorecard receipt;
it does not receive HOLDOUT bytes, report bars, candidate source, or the child's
full summary payload. Existing A01 versions and routes retain their frozen
contracts.

## Limits

This route is operator-triggered local SANDBOX supervision. The service still
depends on real isolated HOLDOUT storage and Windows identity/ACL/process
acceptance. Source authenticity, calibration, mission-level review, independent
adversarial acceptance, and every P00-P13 release gate remain open. A01 remains
PARTIAL/SHADOW with zero confidence, mandatory escalation, and no execution,
promotion, order, or safety authority.

## Rollback

Revert the A01 0.10.0 implementation commit to remove its registry entry,
implementation, typed route, dispatch/source contract, schemas, and this ADR/status
entry. Keep any already written graph receipts and context checkpoints as retained
records; after rollback they are unavailable for new A01 0.10.0 execution and must
not be rewritten or replayed under another manifest version. No database migration
or data deletion is required.

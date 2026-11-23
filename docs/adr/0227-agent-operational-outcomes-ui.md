# ADR 0227: Agent operational outcomes in Mission Control

## Status

Accepted as a protected, read-only PAPER diagnostic. Calibration, health, SLA, and
promotion conclusions remain unavailable.

## Decision

Expose a protected `/api/v1/agents/operational-quality` read backed by the
existing `GraphRuntime.quality_inventory()` and
`operational_quality_observation()` functions. The read reuses the checked graph
receipts, their matching traces, and the pinned agent registry. It adds no
database, lifecycle mutation, execution control, or raw input/output payload.

Mission Control displays run status counts, completed-run share, and available
mean/p95 trace durations grouped by agent ID and exact version. The browser
validates the complete response contract before rendering it. A missing archive,
integrity failure, or history larger than the 512-run quality window yields an
explicit unavailable state; the UI never renders a partial prefix as a complete
summary. Mean and p95 durations are validated independently because skewed timing
distributions can have a mean above the 95th-percentile observation.

## Limits

These measures describe retained operational outcomes only. They do not establish
prediction accuracy, calibration, current runtime health, an SLA, independent
evidence, or promotion eligibility. The local SQLite audit chain is not a defense
against an attacker who can rewrite the database and its audit metadata.

## Rollback

Revert the API and Mission Control increment. It creates no persisted data or
schema migration, so rollback requires no data repair.

## Verification

The increment reuses the existing operational quality aggregation and graph
integrity checks. Verification for the API/UI wiring is recorded in
`docs/BUILD_STATUS.md`; this change adds no new local test cases.

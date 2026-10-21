# ADR 0220: A01 validation review cohort

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

A10 0.2.0 reads an already archived candidate validation result and produces an
advisory summary. A01's existing research cohorts are fixed to A08; changing
those child bindings would break their persisted plan contracts. A parent
supervisor must not receive HOLDOUT evidence or invoke candidate validation.

## Decision

Preserve A01 0.6.0 and 0.7.0. Add A01 0.8.0 as a separate partial SANDBOX
cohort with a typed `/api/v1/sandbox/research/supervise/v4` route. The operator
selects one archived validation artifact. The parent context retains its opaque
artifact reference and server catalog/health/permission evidence; parent-aware
dispatch compiles exactly A10 0.2.0 for that same reference. A10 remains the
only component that reads the checked validation package and HOLDOUT-classified
summary sources.

The parent is limited to one sequential advisory child and stays bound to the
task, manifest, and persisted receipt. Generic PAPER context compilation rejects
A01 0.8.0. A09 construction, candidate execution, promotion, and order authority
remain outside this cohort.

## Consequences

TaskPlanV4 and strict request/output schemas version the new contract. Parent
and child graph receipts remain durable across restart. SANDBOX is an
application permission scope under one local process identity; it does not
provide operating-system isolation or independently permissioned HOLDOUT
storage. A10's existing execution and holdout acceptance gaps remain open.
This increment does not close P07 or P00-P13 acceptance.

## Rollback

Revert the A01 0.8.0 route, registry entry, and dispatch wiring together. No
database migration is introduced. Retained context and graph receipts should
remain in the local archives; older code will not expose the new cohort, and
restored code must continue to validate those archives before use.

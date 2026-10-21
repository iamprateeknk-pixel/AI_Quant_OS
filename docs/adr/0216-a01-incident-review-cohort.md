# ADR 0216: Add a versioned A01 incident-review cohort

Status: Accepted for PARTIAL/SHADOW PAPER use

## Context

A18 0.2.0 has a protected PAPER mission that recomputes deterministic incident
classification from retained health, broker, world, reconciliation and safety
facts. It publishes an immutable proposal for operator review and has no
authority to dispatch that workflow or change safety state. A01 0.4.0 has
declared market, data, event and drift cohorts, but intentionally reports A18
as unsupported. Changing the A01 0.4.0 task set would alter an immutable plan
contract and its schema.

## Decision

Add A01 0.5.0 and a separate authenticated incident-supervision context route.
The route accepts only a bounded task ID and fixes the task kind to
`INCIDENT_REVIEW`; it exposes no child selection, tool, budget or authority
fields. The versioned plan admits only A18 0.2.0 for that cohort and retains
all previous A01 0.4.0 cohorts unchanged.

Catalog and permission snapshots, current health, child quota/admission,
manifest and quarantine checks, the parent deadline, content-bound child
identity and durable graph receipts use the existing local supervision
boundary. A `HEALTH_ERROR` state defers the child. An A18 proposal remains an
operator-review artifact; A01 does not dispatch it or execute a safety,
recovery, order or capital action.

## Consequences and limits

This adds one immutable manifest and two strict JSON schemas; it requires no
database migration. Existing 0.2.0 through 0.4.0 manifests and outputs remain
available. The implementation is PARTIAL/SHADOW and local PAPER only. It does
not add adaptive orchestration, A08/A09/A10/A11 sandbox task routing, external
alerts, reputation, calibration, independent acceptance, distributed
scheduling, or live authority. A18's source-evidence and proposal limits remain
unchanged.

Verification uses the actual protected API, persisted parent/child graph,
proposal store, restart retrieval and schema-parity tests documented in
`docs/TASK_SUPERVISOR_HANDOFF.md`.

## Rollback

Disable use of the incident-supervision route, then revert the A01 0.5.0
route, dispatch and registry wiring, implementation, schemas and associated
tests as one change. No database migration is introduced. Existing versioned
receipts and proposals can remain stored; they are not executed by rollback and
remain readable by the existing graph tooling.

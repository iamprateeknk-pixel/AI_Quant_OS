# ADR 0217: Add a sandbox research cohort to A01

## Status

Accepted for local advisory SANDBOX use.

## Context

A08 0.2.0 drafts bounded research hypotheses from checked TRAINING evidence.
Its data-read tools require SANDBOX scope. Earlier A01 task-supervisor versions
compile PAPER contexts and require PAPER children, so they cannot invoke A08
without crossing that permission boundary. A09 candidate construction also
requires a separate operator-reviewed hypothesis and remains a distinct action.

## Decision

Add immutable A01 0.6.0 with one fixed `HYPOTHESIS_RESEARCH` task kind and one
fixed child, A08 0.2.0. The parent and child both run with SANDBOX scope. The
authenticated context and execution routes accept only a bounded task ID and a
run/context identity; callers cannot select agents, tools, datasets, or scope.
The existing A01 versions and PAPER task cohorts remain unchanged.

Child context compilation uses A08's existing source adapters, dataset
classification checks, quotas, context expiry, and stored checkpoints. Parent
plans bind the original task, exact A08 manifest, deterministic child run ID,
typed receipt, and parent deadline. Health ERROR defers the child. Durable
receipts can be inspected and resumed through the typed SANDBOX route.

## Consequences and limits

This creates a supervised route to the existing A08 mission; it does not add
source generation, model routing, statistical validation, candidate execution,
or promotion. SANDBOX is an application permission scope, not an operating
system boundary. The A09 build still requires its own operator-reviewed
hypothesis. No PAPER order, safety-state, broker, or capital authority is
introduced, and no database migration is required.

## Verification

`tests/test_task_supervisor_research.py` covers authenticated fixed-cohort
dispatch, exact A08-only child binding, restart retrieval, route rejection,
health-error deferral, schema parity, and unchanged PAPER orders/safety state.
The focused A01, A08, graph, API, registry, and architecture group is recorded
in `docs/BUILD_STATUS.md`.

## Rollback

Stop using the typed SANDBOX research-supervision routes, then revert the A01
0.6.0 manifest, implementation, dispatch, route, schemas, tests, and handoff
updates as one change. No database migration is introduced. Preserve existing
contexts and graph receipts; no stored run is replayed automatically by the
older runtime.

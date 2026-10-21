# Declared task supervision

## Available behavior

A01 0.4.0 is PARTIAL/SHADOW. An authenticated owner declares a task at
`POST /api/v1/paper/tasks/supervise/context` using only `task_id` and `task_kind`.
Execute the returned saved context through the existing paper graph route.
The generic agent context route rejects A01 0.4.0 without this declaration.

| Task kind | Fixed child versions | Maximum calls |
| --- | --- | --- |
| MARKET_REVIEW | A07, A06, A05, A02, A04, A03 at 0.2.0 | 6 |
| DATA_REVIEW | A07 0.2.0 | 1 |
| EVENT_REVIEW | A06 0.2.0 | 1 |
| DRIFT_REVIEW | A14 0.2.0 | 1 |

The declaration joins the captured world, catalog, health and permission evidence.
Its task identity, observation time and world version must match the immutable
context. Content-addressed context identity separates different task purposes,
even with the same task ID. Eligible specialists outside the chosen cohort are
explicitly NOT_REQUESTED and their context sources are never fetched.

Each requested child still needs current permissions, source admission, available
role quota and time remaining under the parent deadline. Actual child receipts
remain durable and reusable for the same original context. Quarantined children,
health errors, source failures and quota denial remain typed deferred/failure
outcomes. Failed attempts remain charged; a repeated parent may charge its own
orchestration attempt while reusing children. These are logical call counts,
not token, monetary or physical request measurements.

## Compatibility and limits

Original A01 0.2.0 and 0.3.0 manifests, schemas and fixed six/seven-child behavior
remain available. The new TaskPlan and request schema have separate exports.
The shared orchestration formatter takes trusted plan metadata; caller input
cannot select children, tools, capability scopes or budgets. No SQL migration.

This declaration is owner intent, not a certified task registry or approval.
A01 0.4.0's four review types plus the 0.5.0 incident review cover a bounded
portion of P07. Adaptive classification, queues, scheduling, distributed
execution, repair/evolution integration, full specialist missions, reputation,
calibration and independent acceptance remain pending. Confidence stays zero
and escalation mandatory. No broker, capital, promotion or release authority
is granted. All P00-P13 remain incomplete.

## A01 0.5.0 incident review cohort

The additive route `POST /api/v1/paper/incidents/supervise/context` accepts only
an authenticated `{ "task_id": "..." }` request and compiles a distinct A01
0.5.0 context with the fixed `INCIDENT_REVIEW` declaration. The cohort calls
only A18 0.2.0. Its typed plan binds the exact task declaration, manifest,
source-derived child identity, output hash and child receipt. It reuses the
existing parent deadline, quota, quarantine, health and restart-safe receipt
checks. A health `ERROR` defers A18 and makes no child call. A18's immutable
proposal is retained for operator review; the cohort does not execute a
workflow, transition safety, recover an order, or change PAPER orders.

A01 0.4.0 and its four existing request kinds remain unchanged. The new plan
and input contracts are exported as
`docs/schemas/task-plan-0.5.0.schema.json` and
`docs/schemas/incident-supervision-input-0.5.0.schema.json`. A01 0.5.0 remains
PARTIAL/SHADOW; this does not complete A18's operational alerts, scheduling,
calibration, independent review or the P07/P00-P13 gates.

Verification: `tests/test_task_supervisor_incident.py` covers the actual
authenticated API and persisted graph, exact A18-only dispatch, proposal
retrieval, restart, zero order/safety change, malformed and unauthorized input,
source/plan binding, health-error deferral and schema parity.

## Verification

See `evaluations/task-supervisor-0105.json` for fresh staged-checkout verification,
source fingerprints, exact scope and limitations. Initial four real API task
cases failed with the route absent before implementation. Additional checks cover
owner access, invalid/authority-bearing inputs with zero quota usage, child-only
charging, receipt reuse, original task binding, quarantine before/after context,
health error, source/plan forgery, task-purpose separation, restart and schemas.
Earlier supervisor, evaluation, graph, quota, health and owner-session checks are
included. This increment adds no UI and makes no browser acceptance claim.

## A01 0.7.0 research cohort

A01 0.7.0 adds a separate typed SANDBOX route at `/api/v1/sandbox/research/supervise/v3`, fixed to A08 0.3.0. A01 0.6.0 continues to call A08 0.2.0. The v3 parent and child contracts are version-bound, and integration coverage confirms the persisted parent receipt survives restart without changing PAPER system status. Generic PAPER context compilation rejects A01 0.7.0. Focused tests cover route admission, exact child identity, schemas and restart. This remains advisory and does not close P07 or P00-P13 acceptance. See ADR 0219.

Final verification for A01 0.7.0: the focused supervisor/registry/dispatch/graph/API/A08 group passes (165). Ruff lint and format checks pass, as does `git diff --check`. Bugbot reported no actionable findings but could not collect tests because `aiosqlite` is missing in its review environment; the workspace focused regression completed successfully. An expanded API run found one unrelated trade-intent JSON Schema parity failure in `tests/test_api.py::test_trade_intent_v1_json_schema_matches_request_model`; the full backend suite was not run. A01 0.6.0 remains fixed to A08 0.2.0, and 0.7.0 remains advisory SANDBOX supervision. P07 and P00-P13 acceptance remain open. See ADR 0219.

## A01 0.8.0 validation review cohort

A01 0.8.0 adds the typed route
`POST /api/v1/sandbox/research/supervise/v4/context` with an operator-selected
`artifact_ref`. Its task declaration binds that reference into the parent
context alongside server-owned catalog, health, world, and permission evidence.
The versioned dispatch contract admits exactly A10 0.2.0, and the parent-aware
compiler verifies that the child uses the same artifact before invoking A10's
existing checked validation context compiler. The parent receives neither the
HOLDOUT dataset reference nor A10's summary payload. A10's checked summary
remains advisory and grants no candidate execution, promotion, or order
authority.

The integration test covers the actual route and graph, exact artifact binding,
PAPER order/safety invariance, operator-only typed admission, generic PAPER
route refusal, and restart receipt reuse. The focused supervisor/dispatch/
source/registry/validation API and A08 regression group passes (146) under
Pydantic 2.13.5/Core 2.46.5. The full backend suite passes (3,247 passed,
43 skipped) under that platform profile. SANDBOX remains a local application
permission scope; independent storage/process isolation, full P07 mission
acceptance, and P00-P13 release gates remain open. See ADR 0220.

## A01 0.9.0 reviewed construction cohort

A01 0.9.0 adds an authenticated SANDBOX route at
`/api/v1/sandbox/research/supervise/v5/context`. It requires an operator
provided active `hypothesis-review:<sha256>` reference. Parent context stores
only that reference and server-owned evidence. The versioned catalog selects
only A09 0.2.0, and parent-aware compilation rechecks the exact reference before
using A09's existing operator-reviewed builder gate. A09 produces an inert
candidate with execution and promotion authority false. The route cannot
compile from a withdrawn or inactive review; generic PAPER context compilation
refuses A01 0.9.0.

Integration covers inactive-review and extra-field rejection, exact dispatch,
candidate provenance, unchanged PAPER orders and safety state, generic PAPER
route refusal, and restart receipt reuse. This extends supervision of the
existing review workflow; it does not replace operator review or authorize a
candidate to run. Roll back the A01 0.9.0 registration, v5 route, dispatch
contract and two schemas together; keep existing A08/A09 history and candidate
records readable. See ADR 0221.

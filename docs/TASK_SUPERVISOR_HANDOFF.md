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
Four review types cover a bounded portion of P07. Adaptive classification,
queues, scheduling, distributed execution, repair/evolution integration, full
specialist missions, reputation, calibration and independent acceptance remain
pending. Confidence stays zero and escalation mandatory. No broker, capital,
promotion or release authority is granted. All P00-P13 remain incomplete.

## Verification

See `evaluations/task-supervisor-0105.json` for fresh staged-checkout verification,
source fingerprints, exact scope and limitations. Initial four real API task
cases failed with the route absent before implementation. Additional checks cover
owner access, invalid/authority-bearing inputs with zero quota usage, child-only
charging, receipt reuse, original task binding, quarantine before/after context,
health error, source/plan forgery, task-purpose separation, restart and schemas.
Earlier supervisor, evaluation, graph, quota, health and owner-session checks are
included. This increment adds no UI and makes no browser acceptance claim.

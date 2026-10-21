# A18 local incident review handoff

A18 0.2.0 is PARTIAL and SHADOW. The original 0.1.0 remains planned. Its fixed
role reads world, health and portfolio evidence and proposes incident review;
it has no order, safety transition, recovery, promotion or external alert tool.

## Implemented flow

With operator authentication, compile A18's paper context through
`POST /api/v1/paper/agents/context`, then execute its returned context reference
through `POST /api/v1/paper/agents/execute`. The persisted graph returns an
`IncidentRecommendation` referencing an immutable proposal. Retrieve it through
`GET /api/v1/paper/incidents/proposals/{proposal_ref}` with operator authentication.
The response is archival, including after expiry; it does not extend validity.

Five checked source records cover the complete unresolved local health collection,
paper simulator flags/orders, world freshness, complete local/broker fill identity,
and process/durable safety states. All retained incident codes are counted, with
at most 1,000 incident entries. Paper orders and each fill collection are bounded
at 10,000. Duplicate identities and overflow are refused. Hashes identify the full
collections; compact packets cannot independently reconstruct their raw contents.
There is no independent audit anchor and capture is not atomic across stores.

The deterministic policy never recommends below either observed safety floor.
Missing evidence restricts new risk; mismatched risk state, future ledger facts
and critical retained failures recommend HALT. Live broker health is unavailable.
Confidence is zero, uncertainty one and escalation mandatory. These are descriptive
review recommendations, not calibrated operational decisions.

The sole proposal tool takes an empty payload and requires runtime-bound context
and task identity. It recomputes policy from the exact context, checks current
permissions, expiry and safety floors before and after publication. The separate
`paper.incident-proposals.sqlite3` archive allows 256 immutable proposals, verifying
all rows and their audit binding on reads/writes. Concurrent identical publication
is idempotent. Missing metadata, altered rows or journal damage are refused.
Capacity exhaustion requires operator handling; there is no eviction.

Cancellation joins the owned publication thread. A cancelled, expired or revoked
call can retain a committed archival proposal without producing a usable graph
output. No proposed workflow is dispatched. Actual deterministic risk services
continue to own containment and recovery. Reading evidence deliberately avoids
the existing mutating paper reconciliation control path.

A01 0.2.0 retains its six-specialist wake policy. A01 0.4.0 adds declared
market, data, event and drift review cohorts. A01 0.5.0 adds a separate
`INCIDENT_REVIEW` cohort that dispatches only A18 0.2.0; it does not dispatch
the resulting workflow. See `TASK_SUPERVISOR_HANDOFF.md` and ADR 0216.

## Evidence and remaining work

Four declared synthetic development cases use the actual protected API, graph,
SQLite proposal and exact restart retrieval. The retained report pins five A18
modules, the evaluator and fixture bytes. It does not pin all transitive runtime
or API sources. Schemas are retained for recommendation and workflow proposal.
Tests also cover full collection counts, complete fill comparisons, forged output,
current higher risk floors, expiry/revocation during publication, cancellation,
archive concurrency/capacity/corruption and malformed pure-policy facts.

This is not holdout evidence or production acceptance. Live broker telemetry,
actual operational alert delivery, operator workflow execution, distributed
incident scheduling, calibration and independent review remain open. No candidate
code, repair verifier, live broker or paid provider is executed by this addition.
See BUILD_STATUS for final regression evidence and all remaining release gates.

## Rollback

Stop the owned app and preserve the entire state root, including proposal,
health and graph archives, before reverting. Older code cannot serve this new
mission or its proposals. Preserve the retained fixture/report/source bytes;
do not remove archived incidents or lower safety state to make rollback succeed.

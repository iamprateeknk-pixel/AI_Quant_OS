# Temporal repair recovery checkpoint

## Delivered

`ai_quant_os.temporal_repair` adds an optional Temporal workflow over a pinned
health incident, candidate, and execution owner. Its trusted activities restore
the watchdog's durable safety state, revalidate that the candidate belongs to
the incident, and read the owner-bound execution evidence without starting a
container.

The workflow checkpoints containment and execution inspection in Temporal
history. It waits for an explicit `reconcile` signal when execution remains
unconfirmed, then rereads evidence with a single activity attempt. A completed
successful run stops at `AWAITING_INDEPENDENT_VERIFICATION`. A failed run stops
at `REPAIR_FAILED_AWAITING_REVIEW`. Every result fixes `promotion_allowed` and
`production_released` to false; this workflow does not transition the risk state
to `NORMAL`.

The optional Temporal dependency remains isolated from core imports. The PAPER
API can host this worker when `AI_QUANT_TEMPORAL_REPAIR_ENABLED=1`; this also
requires persistent PAPER storage, operator authentication, the health watchdog,
and `AI_QUANT_TEMPORAL_RECOVERY_ENABLED=1` with a local Temporal target. The
worker binds to the configured Git repository (`AI_QUANT_REPAIR_REPOSITORY`, or
the local project root) and the same health journal used by the watchdog. The
setting defaults off.

The operator API exposes `GET /api/v1/repair/recovery`, `POST
/api/v1/repair/recovery`, `GET
/api/v1/repair/recovery/{incident_id}/{candidate_id}/{run_id}`, and `POST` to
the same path ending in `/reconcile`, plus a bounded signed-certificate
submission route ending in `/verification-certificate`. Every route requires
the operator token. A signed PASS is verified against the separately root-signed
reviewer registry and retained execution/request/replay pins before the workflow
reports `INDEPENDENT_VERIFICATION_PASSED`; a signed FAIL remains review-only.
The start body contains the exact incident ID, candidate ID, execution owner,
and a bounded run ID. Reconciliation only signals the workflow while it is
waiting at an unconfirmed checkpoint; the activity rereads the retained files
and never starts a container.

## Verification

`tests/test_temporal_repair.py` starts the pinned local Temporal server,
restarts the server while a workflow waits at an uncertain checkpoint, and
confirms that the workflow resumes from durable history after an explicit
signal. It also checks that failed execution cannot reach production, that a
critical watchdog incident restores durable `HALT`, and that the execution
receipt is bound to the exact candidate snapshot and incident.

This is durable evidence review, not end-to-end repair automation. Candidate
creation and verifier execution remain separate operator commands. Independent
review of verifier implementation and live isolation, shadow/canary execution,
and rollback automation remain outstanding P10 work. Quote-incident closure is
available separately under the watchdog's fresh trusted quote gate; critical and
agent incident resolution remain gated.

# A01 0.3.0 paper drift supervision

Increment 0103, 2026-10-09. Partial P07/P09/P12 integration; every P00-P13 phase
and live-release gate remains incomplete.

## Version and authority

A new immutable A01 0.3.0 PARTIAL/SHADOW manifest uses the original fixed
WORLD_STATE_READ, HEALTH_READ and TASK_ORCHESTRATE ceilings and five-second
parent timeout. Original 0.1.0 and 0.2.0 manifests remain unchanged. There are now
31 manifest versions across 18 roles, including 13 partial versions for 12 roles.
The new TaskPlan has policy paper-specialist-supervisor-v2, a maximum of seven
sequential child calls under the parent deadline, and mandatory escalation with
confidence zero. It has no order, promotion, credentials or risk-setting authority.

Use the protected existing context/execute routes with A01 agent_version 0.3.0.
The bound parent catalog selects A07/A06/A05/A02/A04/A03 and A14 version 0.2.0.
It excludes itself and explicitly defers all planned, unsupported or quarantined
versions; health ERROR withholds all children. Source validators recompute exact
catalog executability and effective permissions. A01 0.2.0 still selects its six
specialists and explicitly defers A14 and the new supervisor version. Its original
TaskPlan wire schema is preserved. The new schema is task-plan-0.3.0.schema.json.

## Child execution and evidence

The trusted TASK_ORCHESTRATE handler dispatches only declared supervisor versions
with exact runtime-bound parent context/task/version. Clients cannot name children
or replace source/manifest selections. Before every child it verifies the audit,
health, parent deadline and current permissions. Every child gets its own compiled,
persisted, source-bound paper context and durable graph receipt.

A14's actual API context path captures the verified paper owner/fill/initial-price
ledger and declared drift policy. It compares adjacent paper execution windows;
missing policy, samples or benchmarks remain unassessed. Live/shadow/prediction
sources, calibration and causal attribution remain unavailable. The parent retains
only the actual receipt identity/status/output hash and clamps expiry to completed
children. It does not interpret a comparison as a trading or demotion decision.

The existing durable role quota charges A01 parent context/tool attempts and A14
context fetches. All versions share routine ceilings and protected recovery
capacity. An exhausted A14 source budget produces WAIT_FOR_DATA without invoking
A14. Failed capture produces NOT_STARTED_SOURCE; quarantine before selection is
explicitly deferred, and later revocation produces NOT_STARTED_PERMISSION.
Retries/restarts reuse original child receipts without recapture or replay.
Failed or cancelled admitted attempts are not refunded; uncertain outcomes remain
uncertain under the existing graph runtime.

When health monitoring is enabled, child receipts are immediately observed before
the parent finishes; failed/time-out/invalid/uncertain receipts use the existing
health taxonomy and quarantine policy. This observation now covers both supported
supervisor versions and complements the retained scan. Monitoring disabled still
preserves existing health evidence and independent safety controls.

## Verification and remaining scope

Trusted tests exercise new/old actual API graphs and restart, actual retained fill
source capture, damaged benchmarks, zero quota, shared quota ownership/retry,
quarantine before/after parent capture, failure/time-out receipts, immediate health
observation with the background scan held dormant, parent expiry before A14 source
capture, typed output/selection identity and executable-catalog forgery, and both
schema exports. No paid provider, external broker, candidate SDK or proposed code was executed.

Dynamic task-specific routing, full reputation/calibration, all other specialist
missions, autonomous scheduling, full causal live/shadow drift, demotion workflows,
independent evaluation/promotion and complete cost governance remain pending.
No browser/UI acceptance or full-suite release acceptance is claimed.
See evaluations/supervisor-drift-0103.json for final verification evidence.

## Later declared task routing

A01 0.4.0 adds four explicit review cohorts while preserving this version.
See `TASK_SUPERVISOR_HANDOFF.md` and ADR 0105. Full adaptive scheduling remains open.

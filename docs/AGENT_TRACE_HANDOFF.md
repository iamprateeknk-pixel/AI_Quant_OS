# Durable agent graph traces

## Observed behavior

`GET /api/v1/agents/graph-runs/{run_id}/trace` requires current operator admission
and returns the typed GraphTrace schema. It is an archival metadata read; it does
not compile sources, run or resume a graph, consume agent call quota or authorize
risk. Unknown runs return 404; invalid/damaged storage returns 503.

New graph transitions record a UTC wall-clock observation in the existing audit
event, in the same transaction as the original graph record. The configured runtime
clock is used. CREATED, RUNNING and terminal observations therefore refer to state
transitions, separately from original context observation/expiry. Same-context retry
or restart never recreates timestamps. No extra database table or receipt change.

The trace verifies the complete existing audit chain, reads at most three original
transition events for the exact run and reconstructs every historical record hash
from its original checkpoint and receipt. Its stable identity binds run, context
and immutable manifest. Actual completed envelope trace IDs and output hashes link
back to the original output, without returning output/context payloads. The original
scope, task, input references and per-event sequence/hash remain visible.

## Timing interpretation

- AVAILABLE: all recorded transition times exist and are ordered. Completed
  reservations with a RUNNING event have an observed RUNNING-to-terminal interval.
- UNAVAILABLE: all events predate timing instrumentation. No elapsed time.
- PARTIAL: a legacy reservation gains later observed events. No elapsed time.
- CLOCK_REGRESSION: observed UTC wall time moved backward. No elapsed time.

Elapsed time describes the observed reservation interval. It is not CPU time,
monotonic latency, external tool/provider latency, paid usage or the exact completion
time of an uncertain external operation. CREATED-only and currently RUNNING traces
have no elapsed interval. UNCERTAIN marks a recovery observation, not a claim that
an ambiguous call completed then. A trace never establishes current context validity,
calibration, source attestation or order authority.

## Compatibility

Prior GraphReceipt/GraphRecord and all 76 prior schemas remain unchanged. The reader
accepts the exact original five-field transition event or its new observed_at form.
Old archives are read without invented timing. New archives need a compatible reader;
older binaries reject the additive audit field, so preserve compatible readers and
never remove/rewrite timing or erase audit history to roll back. No source/worker
execution or credentialed calls are added.

## Verification and pending work

See `evaluations/agent-trace-0106.json` for fresh staged-checkout scope, hashes and
results. Tests first failed for the missing runtime method and protected route.
Coverage includes real API graphs/restart, exact injected transition times, legacy
and mixed journals, backward time, failures/timeouts/cancellation/recovery, rehashed
historical forgery, rollback, corruption isolation and typed schema claims.

Nested parent/child trace navigation, node/tool/source spans, OTel/export/retention,
aggregate quality/performance metrics, full UI workflows and independent acceptance
remain open. No browser or full release acceptance is claimed. All P00-P13 remain
incomplete.

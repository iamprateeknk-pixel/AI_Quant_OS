# 0089: Observe original source-test worker custody without recovery

Date: 2026-10-08

## Decision

Provide a separate immutable checkpoint report for a pinned original source-only
candidate test job. Reuse its kernel lease, original manifest/source restoration,
exact create/start/removal bindings, confinement admission, terminal-state checks
and source-bound result parser. Bind the report to the package, candidate, request,
manifest, owner and exact hashes of retained metadata files. Export exclusively
outside the original custody root and verify independently pinned canonical bytes.

Distinguish an unacknowledged create or start from retained admission, terminal
exit, removal and result records. Permit historical verification after additional
original metadata is retained; require every reported original byte to remain
available and unchanged. Validate the current inventory as well as the saved
subset. Do not interpret missing metadata as proof that an operation never occurred.

No Docker action, source import, numeric replay, cleanup, retry, result publication,
execution transition or existing wire-model change is added. A report describes
local retained custody, not independent daemon attestation or physical failure.
All cleanup, isolation, confirmed-worker-failure and promotion claims remain false.

## Scope

This covers the source-test worker. Strategy-prefix checkpoints, independent
physical failure attestation, full statistical isolation/acceptance, larger plans,
agent missions and production release remain open. See
`CANDIDATE_TEST_CHECKPOINT_HANDOFF.md` for operator commands and limitations.

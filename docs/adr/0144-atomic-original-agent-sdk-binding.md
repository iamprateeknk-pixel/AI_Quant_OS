# 0144: Bind the original agent archive to observed SDK context atomically

Date: 2026-10-11. Development, PAPER only.

Capture the actual archive span's nonzero SDK trace/span IDs, original runtime
identity and installed SDK version through its owning telemetry runtime. Bind
them to the exact existing agent-run UUID, canonical bytes hash and UTC recorded
time. Capture proves context observation, not export/durable delivery or a
complete trace. Foreign runtime spans and invalid observations produce no link.

The original paper state lock/BEGIN IMMEDIATE owns run insertion, its unchanged
classification event, one immutable trace-binding row and one additional audit
commitment. No separate commit, mutable replacement or late backfill is allowed.
Exact retries preserve the binding; old callers/legacy runs need no binding.
Reads verify canonical bytes, parent-run hash/time and the original audit event;
missing/changed/orphaned rows withhold both link and archive evidence.

The legacy public audit exposes only UUID/hash commitments, not SDK trace/span or
runtime/version fields. Detailed protected lookup is a subsequent deliverable.
No prior wire schema, original table or journal event is rewritten. The new
table is additive, one row per existing bounded run, with 2048-byte row bound.

Binding storage/audit failure rolls back the original advisory archive and returns
its existing sanitized 503. Deterministic Risk and HALT remain available. Export
failure preserves observed context with delivery_verified=false. Persistent
storage retains links across restart; in-memory storage has its existing lifetime.

Rollback disables new capture or reverts writers while retaining rows/events.
Older readers ignore the added evidence and cannot certify its integrity; do not
delete rows, reset audit heads or manufacture historical SDK context. Protected
search/display, order/fill binding, full cross-request lineage, deployment and
independent/full P00–P13 acceptance remain pending.

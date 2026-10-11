# 0141: Count original trace queue outcomes and retain worker custody

Date: 2026-10-11. Development, PAPER only.

Replace opaque SDK batching with an owned bounded implementation of the public
SDK SpanProcessor interface. Keep 512 pending and 32 in-flight batch bounds.
Count each finished span into one coherent inventory: acknowledged, unconfirmed,
dropped, pending or in flight. Allowlist metadata and keep failure text omitted.
All count transitions, liveness capture and instrumentation error reads share
one lock. Producers perform no export I/O.

Retain the exact worker and runtime UUID. A shutdown timeout means STOPPING;
only actual termination means STOPPED. Worker faults retain failure/drop evidence
and never restart automatically. Repeated close observes the same original handle.
Bound flush by a monotonic deadline. Trace outcomes remain volatile and cannot
prove independent delivery or replace the immutable original audit journal.

Expose a typed protected uncached status read, with pre/post authorization,
complete count/batch/liveness validation and empty DISABLED/UNAVAILABLE states.
Preserve all prior wire models, dependency pins and deterministic domain modules.
No read command changes risk/order policy or resets observations.

Rollback by disabling telemetry or reverting 0141 to the prior bounded SDK batching.
Original paper journals and orders remain unchanged; no migration is required.
Keep evidence of any currently live original worker until termination, rather than
replacing it. Diagnostic UI, collector/dashboard deployment, full causal lineage,
independent performance/delivery and all P00–P13 acceptance remain open.

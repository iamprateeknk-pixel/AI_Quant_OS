# 0142: Present original trace export observations in the console

Date: 2026-10-11. Development, PAPER only.

Read the fixed protected tracing endpoint from the Next server using the private
operator session, a three-second request deadline, no-store, refused redirects
and a 4096-byte body limit. No browser API proxy or client credential is added.
Fetch archive and diagnostics concurrently; their source states stay independent.

Select only the typed public observation fields. Validate safe integer counters,
exact inventory with BigInt, batch bounds, original worker liveness and UTC time.
Withhold malformed observations; disabled/unavailable never imply zero outcomes.

Render a server component with semantic count table, snapshot time, worker state,
runtime/exporter details and a native GET refresh. Explain volatile per-runtime
accounting, unconfirmed/lost work and lack of durable delivery verification.
Do not infer health, reset counters, flush or restart workers through a read.

Native API/SDK fixture comparison normalizes only randomly generated runtime IDs.
Explicit fixture flush precedes shutdown to eliminate scheduling-dependent outcomes.
Other worker states are tested as coherent public-contract scenarios.

Rollback by reverting the console panel and fixed read; backend observations,
paper journals and operator controls remain unchanged. Browser launch refusal
is preserved; rendered markup and build checks are not browser acceptance.
Collector/Grafana, alerts, complete causal lineage, production/independent acceptance
and all P00–P13 remain incomplete.

# 0151 — Own cumulative native paper HTTP metrics

## Decision

Attach a native SDK cumulative histogram and failure counter to the actual paper
ASGI lifecycle. Use an instance-owned OTel MeterProvider/reader, explicit resource,
fixed histogram buckets, disabled exemplars and finite route/method/status labels.
Export selected coherent values as protected uncached Prometheus text through the
existing private authorization boundary. Recheck authorization after reading.

This answers request volume, HTTP error volume, ASGI failures and latency bucket
questions with observed native data. Streaming duration includes the full scope;
missing status and terminal body are explicit rather than inferred successful.
The first 128 label combinations retain route detail; overflow retains aggregate
inventory with at most 36 fixed method/status combinations. All samples stay
volatile; disabled/closed/broken metrics withhold samples with 503.

Both instruments record and collect under their own original lock. SDK/format
failure cannot disable deterministic paper controls, reset original tracing,
grant trading/delivery authority, or leak raw exceptions/credentials. No global
provider, extra worker, dependency update, original schema or journal mutation is
introduced. The existing trace collector remains trace-only.

## Evidence and limits

Six initial checks failed against the absent implementation. Exact native SDK
histogram/count/sum, bounded overflow conservation, concurrent records, actual
order/replay observations, private reads/revocation and ASGI exceptions/cancellation
are checked. First integration HALT requests lacked the existing required command
identifier; original rejection evidence is preserved and fixture requests corrected
without changing controls. Wider trusted tests/package identity are recorded in
evaluation 0151. Native Prometheus ingestion, separately admitted scraper/Grafana
deployment, independently justified alerts, load/latency, independent review,
browser and all P00–P13 full acceptance remain pending.

## Rollback

Revert the new native metrics owner, protected route, composition registration and
telemetry middleware/lifecycle delegation in this increment. No migration, archive
rewrite, collector deletion, queue reset or historical retry is required. Metrics
share the optional tracing enablement; disabled remains the launch default.

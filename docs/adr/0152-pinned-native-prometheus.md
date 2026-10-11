# 0152 — Pin and verify local native Prometheus ingestion

## Decision

Pin official Prometheus 3.15.0 Windows amd64 archive size/hash and both native
executable hashes. Provide a fixed bounded local paper scrape configuration with
private header-file authentication and an explicit foreground-only runbook.
No application runtime/schema/dependency, global service, Docker, remote write or
browser change is introduced. Native metrics remain observations without trading,
trace-delivery or independent-audit authority.

Use original owned native Prometheus and Uvicorn handles to verify real ingestion,
SDK histogram/count identity, rejected credential scrape state, retained range
history, hidden credential configuration and disabled lifecycle API. HALT remains
functional when monitoring fails. Only observed terminal handles are released.
Current scrape success is distinct from retained historical samples.

## Evidence and limits

The first run lacked explicit tool admission and skipped two acceptance checks.
The admitted run failed on absent configuration/pins. A first live scrape failed
and the retained native target diagnostic reported seven labels against a bound
of five; corrected the bound to seven based on native evidence. A diagnostic test
indentation error was corrected before rerunning; all originals are retained.
Native parser/config/integration passed after correction. Fresh staged tests and
source identity are recorded in evaluation 0152. Independent signature, Grafana,
alerts, private deployed credential lifecycle, Linux/services, production load/
retention/latency, browser and all full P00–P13 acceptance remain pending.

## Rollback

Revert the additive configuration/pins/runbook/tests. Stop only the original
operator-owned foreground process and retain its data/logs. No application
migration, source rewrite, collector deletion, order retry or journal reset.

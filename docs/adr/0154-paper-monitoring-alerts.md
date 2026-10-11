# 0154 — Evaluate native local paper monitoring incident rules

## Decision

Load separate failed and missing scrape rules in the pinned native Prometheus.
Use a two-minute development noise hold and15-second evaluation interval; these
are not trading SLOs or detection guarantees. Link their annotations to the
checked-out monitoring runbook by repository path. Do not invent a served URL.
Native source failure remains distinct from missing observation and retained
history; no alert performs control/repair, disables authorization or resumes risk.

## Evidence and limitations

The first native rule test failed because the rule file was absent. Native
promtool temporal cases then verified short outages, exact hold/firing/recovery,
missing observations despite unrelated healthy jobs and stale-marker recovery.
The original native server also exposes healthy pending failure evaluation after
its existing actual authorized source token is revoked. It retains the original
metrics history/order inventory and independent HALT behavior. All existing
native ingestion/dashboard tests copy the actual referenced rules along with
configuration; no test-only production expression or shortened hold is used.
Final staged results are recorded in evaluation0154. Existing root/frontend/
dependency/schema identity is checked; no application implementation change.

Local alert/rule APIs are observations, not durable incident custody or operator
acknowledgement. No Alertmanager or external delivery exists yet; Prometheus
cannot detect its own stopped engine. Private roles/service/Linux/load/retention,
all platform alerts/consoles, independent/browser and full P00–P13 gates remain
incomplete. Prior unknown candidate/Docker custody and browser refusal remain.

## Rollback

Revert rule_files/evaluation configuration, additive rules/runbook and related
fixtures/docs. Stop only the known original admitted engine through its existing
lifetime procedure, preserving logs/TSDB; no reset, retry, service installation,
new dependency or order-state migration is needed.

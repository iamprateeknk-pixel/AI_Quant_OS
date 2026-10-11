# Paper HTTP monitoring incidents

This runbook applies to the single admitted local `paper_http` metrics source.
These alerts describe monitoring observations. Order inventory, risk state and
operator controls remain backend-authoritative. A resolved alert is not evidence
of reconciled orders, complete traces or safe recovery of trading authority.

## Scrape failed

`PaperHttpScrapeFailed` means an existing source has continuously reported
`up == 0` for two minutes of successful rule evaluation. The two-minute hold is a
development noise filter across multiple 15-second scrapes, not a trading SLO or
maximum detection guarantee. A shorter failure can remain pending then clear.

1. Record alert name, labels, active time, evaluation health, original process
   identity and current target scrape time. Inspect the admitted Prometheus
   `/api/v1/targets` for this exact target's health and last error. Keep evidence
   private; do not paste raw logs, headers, tokens or configuration into tickets.
2. Check source observation age and the backend's authoritative status/order
   inventory using the existing operator access. Historical metric samples do
   not prove current source health. Do not resubmit an order to test monitoring.
3. A 401 indicates scraper authorization was rejected. Check the privately
   provisioned header file, its access permissions and current backend token
   under the existing credential procedure. Never relax API authorization or
   put credentials into a URL, dashboard or command argument.
4. For connection failure, confirm the exact original backend listener and
   separately owned original Prometheus process. For 503, inspect whether the
   backend's optional metrics owner is disabled, closed or unavailable. For
   limits/parser/timeouts, retain the original response/diagnostic privately and
   compare with the pinned config and SDK metric schema. Do not increase limits
   or restart unknown processes simply to make the observation green.
5. If operational safety calls for a control change, use the existing authorized
   backend NO-NEW-RISK, FLATTEN-ONLY or HALT workflow and its original idempotency
   identity. Verify backend acknowledgement; the alert engine has no control
   authority. No automatic resume follows an alert clearing.

Close the monitoring incident only after the original admitted source has fresh
successful scrapes, its expected original SDK metrics are readable, evaluations
are healthy and the alert is inactive. Record the cause and correction, retaining
the original incident evidence. Separately verify any safety/reconciliation gate.

## Scrape missing

`PaperHttpScrapeMissing` means there is no currently selectable `up` observation
for `paper_http` for two minutes of rule evaluation. It covers removed targets,
stale source observations and a rules configuration with no matching target.
A failed scrape that still emits `up == 0` is covered by Scrape Failed instead.

1. Confirm the original Prometheus process is running and its rule group is
   loaded and healthy through `/api/v1/rules?type=alert`. Confirm the original
   admitted configuration through `/api/v1/status/config`; do not reveal private
   files. Check the fixed target inventory in `/api/v1/targets`.
2. Compare the job label, exact metrics path and target address with the admitted
   config. A healthy unrelated job does not replace this missing observation.
   Retained range samples do not replace current source identity.
3. Check the original source timestamp and any stale marker. Without a stale
   marker, Prometheus lookback can continue selecting an old sample before it
   becomes absent. The hold begins when the expression becomes true, not when
   the backend originally stopped. Do not infer a two-minute outage bound.
4. Preserve the original TSDB/config/logs and incident times. Correct only a
   confirmed configuration/source problem through the established admission
   procedure. Do not reset a journal, queue, TSDB or unknown process.

Resolve after this exact source again yields fresh selectable observations, the
rule evaluates healthily and is inactive. A present observation with `up == 0`
requires the Scrape Failed procedure; presence alone is not successful ingestion.

## Reading and delivery limits

The pinned native Prometheus exposes local alert state through `/api/v1/alerts`
and rule evaluation through `/api/v1/rules?type=alert`. Alert annotations carry
this repository-relative `runbook_path`; resolve it in the checked-out operator
pack. It is not a fabricated served URL. Alert rule state is local to the
original engine and may be lost/reset by restart; this is not a durable incident
journal or acknowledgement queue. A scrape recovering clears these alerts
without a human acknowledgement and without changing any order/safety state.

No Alertmanager, notification receiver, external message, paging service or
automatic repair/control action is configured. If Prometheus itself stops, it
cannot evaluate or deliver its own missing-source alert. External engine-health
monitoring and durable notification/acknowledgement require separate acceptance.
This runbook does not supply production recovery authority, failover, complete
platform incident coverage or independent operating evidence.

Rule semantics and native tests follow the official [alert rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)
and [rule testing](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/) documentation.

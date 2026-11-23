# Native paper HTTP and agent metrics — increments 0151/0226

With optional paper tracing enabled, the same app owns a separate native OTel
SDK 1.45.0 MeterProvider and cumulative reader. No global provider, periodic
exporter, network worker, ambient resource attribute or trace exemplar is used.
Metrics remain disabled with the default `AI_QUANT_OTEL_ENABLED=0`.

## Protected read

`GET /api/v1/observability/metrics` requires the existing private operator token
or unexpired session before and after the read. It returns uncached UTF-8
Prometheus text 0.0.4, bounded to 1 MiB. Disabled, closed, incoherent or broken
metrics return 503 with a fixed message, without fabricated zero samples. The
`X-Paper-Metrics-Accounting: VOLATILE_SDK_HTTP_AND_AGENT_EXECUTIONS` header states
its scope.
No browser credential or public unauthenticated metrics port is introduced.

Four native SDK instruments are exposed:

- `ai_quant_paper_http_duration_seconds`: cumulative histogram of elapsed ASGI
  scope time, including streaming body time, ending at normal return or exception.
  `_count` is the request inventory; `_sum` and `_bucket` support latency queries.
- `ai_quant_paper_http_failures_total`: exceptions, cancellation or return without
  an acknowledged terminal response body. This differs from completed HTTP error
  responses, which remain visible through the histogram's status-class label.
- `ai_quant_paper_agent_execution_duration_seconds`: elapsed monotonic time from
  durable RUNNING admission to a terminal graph receipt. Graph attempts denied or
  deferred during admission are measured through their terminal decision.
  UNCERTAIN recovery observations have no attributable execution interval.
- `ai_quant_paper_agent_executions_total`: terminal graph attempts by fixed agent
  ID and outcome. These operational counts are not completion quality, model
  accuracy, strategy performance, or promotion evidence.
  UNCERTAIN recovery transitions increment this counter but intentionally have no
  matching duration sample.

Labels are only registered route template, fixed HTTP method and fixed response
status class. Missing response status stays `UNKNOWN`. Raw path/query, request
body, credentials, order/agent IDs, trace IDs and exception text are omitted.
The first 128 complete label combinations remain separate. Additional combinations
are aggregated under `OTHER_ROUTE`, retaining method/status and every observation;
at most 36 additional overflow combinations are possible. This deliberately loses
route detail after capacity. Unknown routes use `UNMATCHED` without raw path data.
Successful scrapes themselves enter the next observation after their body ends.
Agent labels are limited to fixed A01–A18 and terminal status values (at most 162
combinations). Task/run IDs, agent versions, contexts, sources, provider names,
outputs, and prompts are omitted. Every non-UNCERTAIN counter must match exactly
one duration series; UNCERTAIN has a count only. This distinction is enforced
before exposition.

The SDK's non-cumulative buckets are converted to cumulative Prometheus buckets
in increasing order, including `+Inf == _count`. Counts remain strict integers;
incoherent/native-invalid observations withhold the entire scrape. A single owner
lock covers both instrument records and collection; repeated reads never reset
counters. Resource and context do not become labels. Duration is native monotonic
elapsed nanoseconds converted to SDK seconds, not a wall-clock deadline or SLA.

## Dashboard queries for a separately admitted scraper

These expressions are usable once an authorized Prometheus source actually exists:

```promql
sum(rate(ai_quant_paper_http_duration_seconds_count[5m]))
sum(rate(ai_quant_paper_http_duration_seconds_count{status_class="5xx"}[5m]))
sum(rate(ai_quant_paper_http_failures_total[5m]))
histogram_quantile(0.95, sum by (le) (rate(ai_quant_paper_http_duration_seconds_bucket[5m])))
sum by (agent_id,status) (rate(ai_quant_paper_agent_executions_total[5m]))
histogram_quantile(0.95, sum by (le) (rate(ai_quant_paper_agent_execution_duration_seconds_bucket[5m])))
```

The provisioned Grafana dashboard includes HTTP panels and agent attempt-rate
and bucket-estimated p95 panels. The agent panels expose fixed agent/status
labels and label attempt counts as operational activity, not quality. Missing
attributable duration samples remain absent; p95 is not an SLA. These panels do
not assert current throughput or a current source health state. Use existing
private server credentials through separately configured scraper headers, never
committed config values or UI inputs. Scraper/service deployment, credential
lifecycle, native Prometheus ingestion, actual dashboard render acceptance,
justified alerts and delivery tests remain pending. The collector's trace-only
configuration is unchanged; metrics are not sent to its OTLP receiver.

## Verification, lifecycle and rollback

Native tests check exact HTTP and agent bucket boundaries/count/sum, repeated
cumulative reads, overflow conservation, concurrent SDK records, actual order/replay API
observations, secret omission, authentication/revocation, disabled/broken reads,
record failure without changed orders and actual ASGI exception/cancellation.
App shutdown closes its metrics owner without resetting/restarting trace workers.
SDK metric errors are counted as instrumentation errors and do not change paper
risk/order/HALT authority. Counts are volatile and restart begins a new inventory.
No original journal/schema/dependency pin changes are made. Revert ADR 0151's
additions to roll back; no migration or data cleanup is required.

The full suite is withheld to preserve uncertain original 0084/0088 candidate
custody. Independent review, latency/load/production guarantees, durable telemetry,
full causal tracing, browser acceptance and every P00–P13 gate remain incomplete.

Source reviewed 2026-10-11: [official Prometheus exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/).
Native SDK interfaces were checked in the installed 1.45.0 source.

# 0214 — Correlate safe HTTP failure summaries with PAPER traces

## Decision

Emit a single fixed-format local ERROR summary when an HTTP request raises,
returns an incomplete response, or completes with a 5xx status. Include only the
runtime-generated PAPER trace ID (when enabled), a validated route template, a
fixed method enum, and a bounded status. Keep route failure counts in the existing
finite-label Prometheus histogram/counter. Do not export IDs as metric labels.

The middleware remains fail-open: logging failure cannot change the response,
control decision, order result, or risk state. Client trace context is not adopted.

## Limits

The summary supports lookup in the protected retained-trace reader; it is not a
durable log journal and is not ingested by Prometheus, Grafana, or the Collector.
The ASGI middleware does not configure the surrounding server's traceback policy.
The terminal/server logger may independently emit exception details and must be
reviewed before logs are forwarded or retained outside the local workstation.

## Evidence

`tests/test_paper_http_metrics.py` verifies an actual generated trace ID appears
in failure summaries, 5xx responses increment the failure metric, route templates
are retained, and raw paths, query values, and exception messages are omitted from
the middleware summary. All existing order, risk, export, and trace-parentage
behavior remains covered by the telemetry regression suite.

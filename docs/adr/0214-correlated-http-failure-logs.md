# 0214 — Correlate safe HTTP failure summaries with PAPER traces

## Decision

Emit a single fixed-format local ERROR summary when an HTTP request raises,
returns an incomplete response, or completes with a 5xx status. Include only the
runtime-generated PAPER trace ID (when enabled), a validated route template, a
fixed method enum, and a bounded status. Keep route failure counts in the existing
finite-label Prometheus histogram/counter. Do not export IDs as metric labels.
Limit summaries to 60 per monotonic one-minute process window; trace and metric
recording continue when a summary is suppressed.

The middleware remains fail-open: logging failure cannot change the response,
control decision, order result, or risk state. Client trace context is not adopted.
The local Uvicorn launcher applies a default-handler filter that replaces any
exception record and traceback with `ASGI exception details withheld` before
formatting it, so exception values and stack locals are not written by that path.

## Limits

The summary supports lookup in the protected retained-trace reader; it is not a
durable log journal and is not ingested by Prometheus, Grafana, or the Collector.
This filter is installed only by the supplied local Uvicorn launcher. Direct ASGI
embedding or a separately configured server needs its own log policy. Summaries
are process output, not a durable journal, and are not ingested by Prometheus,
Grafana, or the Collector.

## Evidence

`tests/test_paper_http_metrics.py` verifies an actual generated trace ID appears
in failure summaries, 5xx responses increment the failure metric, route templates
are retained, and raw paths, query values, and exception messages are omitted from
the middleware summary. A subprocess test configures Uvicorn's actual error
logger and confirms exception messages and tracebacks are withheld. All existing
order, risk, export, and trace-parentage behavior remains covered by the telemetry
regression suite.

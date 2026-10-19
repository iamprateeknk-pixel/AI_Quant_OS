# Paper OpenTelemetry runtime

Development increment 0140, 2026-10-11. This provides actual SDK traces and a
bounded local OTLP transport. It does not complete the full observability service
or certify every live order's trace chain. All P00–P13 remain incomplete.

## Enable explicitly

Install the all-extra hash lock for your supported Python profile, including the
new `observability` extra. SDK/protobuf encoder are pinned to 1.45.0 to match the
already locked API. The four added distribution pins are identical in Windows and
Linux profiles; existing pins are preserved. Actual Linux runtime verification is
pending. A binary-only locked Windows install and native SDK tests were run.

For the managed workflow, `ai-quant-local` starts the pinned Windows Collector
before the API. In PowerShell, set:

```powershell
$env:AI_QUANT_OTEL_ENABLED = '1'
$env:AI_QUANT_OTEL_COLLECTOR_MANAGED = '1'
$env:AI_QUANT_OTEL_ENDPOINT = 'http://127.0.0.1:4318/v1/traces'
ai-quant-local --state data-local/local-app
```

The managed mode verifies and validates the Collector, binds it to loopback,
retains rotated trace output at
`data-local/local-app/observability/paper-traces.jsonl`, and closes it after the
API. Leave `AI_QUANT_OTEL_COLLECTOR_MANAGED` at `0` when starting a Collector
separately. Use the existing local launcher arguments described in
LOCAL_APPLICATION_HANDOFF.md. The API receives only its telemetry enable flag,
loopback endpoint, and (in managed mode) local trace-file path; none go to the
frontend. The normal ASGI app also reads this configuration directly. `0` is the
default enable flag; other values are rejected. Only an explicit literal
IPv4 loopback HTTP endpoint, nonzero port and exact `/v1/traces` path are admitted.
Remote targets, credentials, query strings, fragments and redirects are refused.

## Traces and ownership

Each HTTP request owns a fresh SDK SERVER span. External `traceparent` and baggage
are not adopted. `X-Paper-Trace-Id` returns the generated trace identity when the
SDK has a valid span. Route **templates**, fixed methods and status codes are
allowed; raw URLs, headers, bodies, user IDs, strategy names and evidence refs
are omitted. SDK resources are constructed explicitly, without inherited resource
attributes or exporter headers. No global tracer provider is installed.

The paper intent path records child spans for its market snapshot lookup,
deterministic risk evaluation and order submission. Accepted internal order UUIDs,
native risk decision and order status are allowed. Replayed orders have a replay
span rather than a second submission span. Pending recovery is separately named.
Exceptions propagate unchanged, but telemetry records a fixed failure description
without the exception text, stack or event. This is observation of the real path,
not an alternate risk/execution route. Deterministic domain code is unchanged.

The app owns and closes environment-created telemetry on every lifespan exit,
including in-memory and failed service startup paths. Injected telemetry remains
the caller's responsibility; flush and close it explicitly. Runtime instances are
for one application lifetime. The default creates no SDK export worker.

## Bounded, observed export

Increment 0141 replaces opaque SDK batching with the owned public SpanProcessor
implementation. It preserves 512 pending spans and 32-span batches while counting
exact acknowledged, unconfirmed, dropped, pending and in-flight inventory under
one lock. Only the original worker performs I/O. The official encoder and bounded
local transport remain unchanged: 256 KiB batches, 4 KiB acknowledgements, no
retries/proxies/redirects/ambient credentials or remote error text. Inactivity
socket timeouts are not a strict total export deadline.

`GET /api/v1/observability/tracing` exposes protected original runtime observations
with pre/post authorization. It withholds disabled or incoherent counters. Read
and response time are distinct from the current HTTP request's later span end.
Exporter acknowledgement is not durable delivery; partial acknowledgement keeps
the whole batch unconfirmed. Counts are volatile and never reconcile a restart.

Flush now waits against a monotonic deadline. Shutdown records pending drops and
waits at most three seconds on the original worker; timeout retains STOPPING and
its identity/in-flight custody. Only actual handle termination means STOPPED.
Worker faults retain FAILED and never restart. Existing `state()` keeps its
original shape; use the typed observation for actual worker state. See
TRACING_DIAGNOSTICS_HANDOFF.md and ADR 0141. No trace read resets, refunds or
weakens original risk/order/control behavior.

## Evidence and remaining work

Native SDK tests verify HTTP/child parentage and response identity, original order
replay, HALT with zero orders, omission of secret input and exception data, actual
local protobuf decode, rejection of partial acceptance, blocked worker/full queue
with accepted orders, and original owner cleanup. All test collector threads and
sockets are owned and joined. See evaluation 0140 and ADR 0140.

Collector deployment/attestation, Grafana dashboards/alerts, diagnostic Mission Control
presentation, persistent audit-to-trace references, cross-request market/agent/fill
lineage, agent/provider spans, metrics/log correlation, production sampling and
load/drop/latency acceptance, independent critical review and every full release
gate remain pending. Unknown Docker custody and prior browser refusal are preserved.
No real provider, broker, candidate, credential or capital was used.

## Runtime parent isolation — increment 0150

Internal SDK steps now inherit only the original `PaperTelemetry` instance's
Python context. Foreign active spans and other owned runtimes cannot substitute
parents. Explicit HTTP roots still start fresh; native task context and
`asyncio.to_thread` preserve exact original parentage across concurrent work.
Normal and exceptional exit restore the previous parent; later standalone steps
start fresh. Arbitrary new threads and processes require explicit context
propagation and are not silently joined. Three native regressions reproduced the
earlier ambient-parent adoption. See ADR/evaluation 0150. Span attributes, queues,
transport, journals, domain behavior, schemas and dependency pins are unchanged.

References reviewed on 2026-10-11:
[official instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/),
[SDK tracing](https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.html),
[pinned SDK release](https://pypi.org/project/opentelemetry-sdk/1.45.0/).
# Native local collector added in 0147

`infra/observability/` now provides a pinned Collector Contrib 0.162.0 Windows
development configuration and runbook. Actual paper order/replay spans were sent
through its native OTLP HTTP receiver into rotating JSON files. Original SDK IDs,
parentage and omitted private input were verified; original file bytes survived
collector termination and a separately admitted restart. Collector outage left
paper orders functional and original SDK outcomes unconfirmed. No Docker state,
application dependency or disabled launch default changed. Alpha file exporter,
local-process trust, retention and lack of independent/power-loss guarantees are
explicit. Linux/service/Grafana/search/full release acceptance remain pending.

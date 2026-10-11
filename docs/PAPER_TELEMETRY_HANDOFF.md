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

Start an independently configured local collector accepting OTLP HTTP/protobuf at
`http://127.0.0.1:4318/v1/traces`. In PowerShell, set:

```powershell
$env:AI_QUANT_OTEL_ENABLED = '1'
$env:AI_QUANT_OTEL_ENDPOINT = 'http://127.0.0.1:4318/v1/traces'
ai-quant-local --state data-local/local-app
```

Use the existing local launcher arguments described in LOCAL_APPLICATION_HANDOFF.md.
The launcher forwards only these two telemetry fields to the backend; none go to
the frontend. The normal ASGI app also reads this configuration directly. `0` is
the default enable flag; other values are rejected. Only an explicit literal
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

## Bounded, lossy export

The SDK batch worker has a 512-span queue and 32-span batches. Only this worker
performs transport I/O. The official encoder produces actual OTLP protobuf.
Transport is one attempt, no retries/proxies/redirects/ambient credentials, with
two-second operation timeouts and an elapsed response deadline. Batches are capped
at 256 KiB; acknowledgement is capped at 4 KiB. Invalid/partial acknowledgements
and non-200 responses count as export failures without retaining/logging response
text. One in-flight operation may finish after the response deadline because socket
timeouts are inactivity bounds. Long-lived streaming requests finish their span
when the request ends.

`app.state.paper_telemetry.state()` exposes enabled state and failed **batches**;
it is not a delivered-span count. A full SDK queue can drop spans. `flush()` asks
the SDK to flush and is an administrative operation, not part of order handling;
its timeout argument is subject to the pinned SDK's implementation. `close()`
stops new observations and drops pending queued exports while allowing the
bounded in-flight local operation to finish. This telemetry is not a durable audit
journal or release certificate. Export failures, queue saturation and broken span
creation cannot weaken deterministic decisions, disable controls or alter receipts.

## Evidence and remaining work

Native SDK tests verify HTTP/child parentage and response identity, original order
replay, HALT with zero orders, omission of secret input and exception data, actual
local protobuf decode, rejection of partial acceptance, blocked worker/full queue
with accepted orders, and original owner cleanup. All test collector threads and
sockets are owned and joined. See evaluation 0140 and ADR 0140.

Collector deployment/attestation, Grafana dashboards/alerts, protected diagnostic
status, persistent audit-to-trace references, cross-request market/agent/fill
lineage, agent/provider spans, metrics/log correlation, production sampling and
load/drop/latency acceptance, independent critical review and every full release
gate remain pending. Unknown Docker custody and prior browser refusal are preserved.
No real provider, broker, candidate, credential or capital was used.

References reviewed on 2026-10-11:
[official instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/),
[SDK tracing](https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.html),
[pinned SDK release](https://pypi.org/project/opentelemetry-sdk/1.45.0/).

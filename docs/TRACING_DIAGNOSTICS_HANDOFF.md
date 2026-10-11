# Original trace queue observations

Development increment 0141, 2026-10-11. PAPER scope. Actual SDK spans now use an
owned bounded processor with explicit volatile accounting. This is not full P12
observability or a delivery/release certificate. All P00–P13 remain incomplete.

## Protected read

`GET /api/v1/observability/tracing` uses the existing operator token or session.
Authorization is checked before and after observation; revocation during a read
withholds the entire response. The response is uncached and contains a UTC read
time, original runtime identity, actual installed SDK version, exporter kind,
original worker liveness and bounded counters. Endpoint addresses, credentials,
request bodies and exception text are omitted.

The read captures queue outcomes and instrumentation errors under the same lock.
Invalid/incoherent sources return UNAVAILABLE with no identity or counters.
DISABLED also carries no inferred counters. The current HTTP request ends its SDK
span after this observation, so it is not yet in the finished-span inventory.
Repeated reads and other requests can therefore change the next observation.

Counts cover one in-memory runtime, identified by its generated UUID. They do not
survive a process restart. Never combine different runtime identities into an
original inventory or assume a new zero count reconciles earlier loss. This is a
point-in-time observation; no health lease, automatic recovery, reset, retry or
collector probe is attached to the read.

## Exact inventory

The following identity is checked before publication:

```text
finished SDK spans = exporter-acknowledged + unconfirmed export outcomes
                  + dropped + pending queue + in-flight batch
```

Acknowledged means the configured exporter returned SUCCESS. It does not prove
durable receipt, downstream availability or an independently verified complete
trace. Partial OTLP acknowledgements count the entire batch as unconfirmed: some
members may already have been received. No retry is implied. Failed batches and
unconfirmed spans are retained without remote error text. SDK span lifecycle/
attribute errors are counted separately; failed creation is not a finished span.

The owned queue accepts at most 512 pending spans and dispatches at most 32 per
batch. A full queue drops the new finished span and records the exact drop;
stopping/failure also records each discarded span. These are aggregate drop counts,
not causal attribution of historical loss. Producer callbacks perform only short
locked memory operations. Export and exporter shutdown run on the original worker.
No new dependency, global SDK provider, kernel/risk change or broker path is added.

## Worker and shutdown custody

The processor implements the installed SDK's public SpanProcessor interface;
subclassing retains its required span-ending lifecycle hook. No foreign private
SDK queue or thread fields are inspected. All liveness comes from the worker this
runtime actually created and retains.

RUNNING requires an observed live worker. Shutdown stops admission, records and
discards pending spans, and waits at most three seconds on the original handle.
An in-flight export or cleanup can remain live as STOPPING. Repeating close waits
on that same handle; it never starts/replaces a worker or resets identity/counts.
STOPPED requires observed worker termination and no pending/in-flight work. FAILED
retains a measured worker fault, unconfirmed batch and discarded queue; its liveness
field also exposes a worker still finishing cleanup. Subsequent spans are dropped,
not silently retried. Runtime-generated observations never grant capital authority.

`flush(timeout_millis=...)` waits with a monotonic deadline and reports timeout or
failed/stopping worker. It is an explicit administrative operation; the protected
read never flushes. Existing `state()` retains its two-field interface; `enabled`
describes configuration, while failed-batch count comes from the original queue.
Use the typed observation for worker state. A collector/network fault, queue loss,
broken span or source-status read cannot disable deterministic Risk or controls.

## Native evidence

Native SDK tests retain one blocked batch while completing 632 spans: exactly 32
in flight, 512 pending and 88 dropped. A one-millisecond flush times out; after
release, 544 are exporter-acknowledged and 88 remain dropped under the same UUID.
Shutdown during a blocked export remains STOPPING with original identity/counts
until that same worker finishes. Unexpected worker exit retains FAILED and counts
new drops without restarting. Actual local OTLP decode, original order/replay/HALT,
secret suppression, source failure, session revocation and schema checks are also
covered. See evaluation 0141 and ADR 0141.

## Console presentation

Increment 0142 adds a protected server-rendered diagnostic panel on `/traces`.
The fixed Next server read forwards only the private session, rejects redirects,
uses no-store and a three-second timeout, and limits the response to 4096 bytes.
An allowlisted parser validates the inventory with exact BigInt arithmetic,
batch bounds, worker state and UTC read time before displaying any counts.
Malformed, disabled and unavailable sources infer no zero counters.

The panel shows eight original outcome/batch counts, separate recording errors,
worker liveness, read time and expandable original runtime/exporter metadata.
Warnings identify dropped, unconfirmed and failed recording work. A native GET
refresh requests a later snapshot; it cannot flush/retry/reset/restart export.
Archived graph evidence and export observations are fetched concurrently and
retain independent state. Existing archive pagination and detail views remain.

Native fixture tests compare actual protected API snapshots and SDK-ended spans
in RUNNING, STOPPED and DISABLED states. Only generated runtime UUIDs are normalized.
Explicit fixture flush before close makes terminal counts deterministic. Other
failure/stopping scenarios exercise the public parser/markup contract. Full Node
regressions and a production Next build verify source behavior; actual browser
rendering, focus, overflow, responsive/theme and accessibility acceptance are
pending because the earlier browser launch refusal is preserved.

Calibrated alerts, collector/Grafana
deployment, protected trace search, durable audit/span binding, complete cross-
request market/agent/risk/order/fill lineage, metrics/log correlation, production
sampling/load/drop/latency acceptance and independent critical/release review
remain pending. Full-suite candidate execution and uncertain Docker custody are
untouched; prior browser launch refusal remains in effect.

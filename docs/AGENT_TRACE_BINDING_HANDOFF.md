# Original agent archive to SDK context binding

Development increment 0144, 2026-10-11. PAPER only. This supplies original context
correlation for the advisory classification archive, not complete system tracing.

## Captured source

The actual `paper.agent.archive` span supplies its nonzero SDK trace and span IDs.
Only the telemetry runtime that produced the PaperSpan can capture its context;
foreign/disabled or invalid sources produce no binding. The original runtime ID
and installed SDK version are retained, even if that original worker has since
stopped. No global SDK context, caller traceparent or remote exporter data is used.

The typed binding contains the existing advisory run UUID, SHA-256 of its canonical
model bytes, matching UTC recorded time, SDK IDs/runtime/version, fixed PAPER
scope and OBSERVED_ARCHIVE_SDK_CONTEXT kind. `delivery_verified` is always false.
The SDK span is still active when context is captured and the journal commits.
It can subsequently be dropped or remain unconfirmed; a saved link is not proof
that the exporter acknowledged or retained any span. This distinction also holds
after export failure and process restart.

## Original transaction and reads

`PaperStateStore.record_agent_run(..., trace_binding=...)` uses the original owner
lock and BEGIN IMMEDIATE. It atomically inserts the unchanged agent-run bytes and
classification event, a bounded immutable link and a second audit commitment.
There is at most one link per original run; run capacity remains 10000 and each
binding is bounded to 2048 bytes. No independent writer or commit is introduced.
Persistent paper SQLite retains these across restart. Existing in-memory stores
retain only their existing lifetime.

The new audited event includes the original run UUID/hash and the complete binding
bytes commitment. SDK trace/span IDs, runtime and version are omitted from that
event because the existing legacy audit endpoint is public. The actual binding
is available through the store's verified reader; protected HTTP lookup and
console integration remain pending.

Exact same-binding retries are idempotent. Existing caller retries without a
binding preserve any original link. A different context cannot replace it, and
an old unbound run cannot infer a new context through a later call. Disabled or
broken instrumentation continues the original archive flow with no fabricated link.

`agent_trace_binding` and existing `agent_run` reads verify the complete owning
audit chain, unchanged parent-run commitment, canonical binding bytes and matching
binding audit event under the original read transaction. Edited rows fail even
with locally recomputed digests. A removed row with retained event is corruption,
not a missing/disabled trace. Orphaned or conflicting state is withheld.

Binding row/audit write failure rolls back the entire original advisory archive;
the API returns its existing sanitized storage 503. It does not disable Risk,
change quotas or create orders. Native HALT remains available. No partial archive
success, retry, reset or reconstructed custody is inferred.

## Verification and remaining scope

Native SDK/API tests match actual response trace header, SDK archive span ID,
runtime ID, original run hash and persisted/reopened link. SQLite trigger faults
on row insertion and audit append prove all-or-none rollback. Other checks cover
exact retry, legacy backfill rejection, changed/deleted rows, foreign runtime,
disabled/broken context, failed exporter, secret omission, public audit privacy,
schema identity and actual HALT after a storage fault. Providers are trusted test
doubles; no live provider/broker or proposed candidate code is executed.

Prior schemas, both locks, frontend, deterministic domain and original agent
envelope/repository remain unchanged. Existing state-store legacy formatting is
preserved; newly added methods are formatted locally. See evaluation 0144.

Rollback retains the new table and audit evidence. Older readers can ignore it
but cannot certify its binding integrity. Protected lookup/search and console
presentation, other role/graph and risk/order/fill binding, complete causal lineage,
collector/Grafana/alerts, production/independent acceptance and all P00–P13 remain
incomplete. Browser refusal and uncertain original candidate/Docker custody are
preserved.

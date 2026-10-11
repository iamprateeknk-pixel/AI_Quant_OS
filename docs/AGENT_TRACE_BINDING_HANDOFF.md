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
is available through the store's verified reader and the protected exact-run
lookup described below. Console integration remains pending.

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

## Protected exact-run lookup — increment 0145

`GET /api/v1/observability/agent-runs/{run_id}/trace-binding` accepts one canonical
lowercase advisory-run UUID. Existing operator token/session authorization is
required before source access and rechecked after capture. Revocation withholds
the entire result. Successful typed responses use Cache-Control: no-store.

The original state store captures parent run, binding and complete verified audit
prefix under one lock/SQLite BEGIN snapshot. It checks the same prefix again before
returning. UTC read time is captured after the source read, avoiding a timestamp
that predates evidence committed while waiting for the source lock. The response
includes requested UUID, local source head/
count and available binding only; task/world/evidence/rationale never cross this
boundary. No source path, raw error, original credential or collector endpoint is
returned. Parent/binding models and requested identity are revalidated.

AVAILABLE means verified retained context in that prefix, not an available SDK
trace/collector or current agent permission. NOT_RECORDED identifies a verified
original run without a recorded link; NOT_FOUND identifies no original run in the
captured prefix. Both have no binding. Corrupt/malformed/future-dated evidence is
UNAVAILABLE with no binding/head/count. The local prefix is point-in-time evidence,
not an independent immutable attestation or health lease. Delivery verification
and independent attestation are always false.

The read never resets/backfills/retries/flushes SDK work. Actual SDK/API tests prove
original context/prefix matching, auth before source access, revocation during
capture, unchanged head, no inferred context in missing states, corrupt-row
withholding and actual HALT after a lookup fault. Source-date/identity and strict
head/count corruption cases also withdraw the entire observation. Schema and
broader regressions are documented in evaluation 0145 and ADR 0145. No browser
or frontend source was changed; detailed console lookup remains pending.

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
but cannot certify its binding integrity. Detailed search and console
presentation, other role/graph and risk/order/fill binding, complete causal lineage,
collector/Grafana/alerts, production/independent acceptance and all P00–P13 remain
incomplete. Browser refusal and uncertain original candidate/Docker custody are
preserved.

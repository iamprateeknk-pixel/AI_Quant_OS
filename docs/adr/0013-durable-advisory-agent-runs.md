# 0013: Durable advisory agent runs bound to audit history

## Contract

Store a schema 1.0 `AgentRun` containing the fast decision, deep review, original snapshot reference and recorded time. The run ID is the fast trace ID; the review retains its own unique trace ID. Validate world-state, task, parent trace, instrument, input references, confidence/uncertainty and ordered expiry across the bundle. Fast escalation must remain true. Non-completed reviews cannot contain candidate classification/rationale, confidence or contradictions. A completed review requires bounded rationale and nonempty evidence.

`GET /api/v1/agents/runs/{run_id}` returns verified historical envelopes with PAPER mode and `archival_only=true`. `current` describes the original review time window; it does not establish agreement with current market data or permission to execute. Expiry is never extended. Missing runs return 404. Invalid audit, missing/corrupt snapshot, invalid source provenance or result corruption returns sanitized 503 responses. Existing local read access applies; production operator sessions and result access policy remain unfinished.

The API verifies both envelopes against the archived instrument's evidence and source timestamps on creation and retrieval. It never reloads market quotes or calls a provider on retrieval. Rationale is preserved as untrusted text and must not become tool instructions or executable markup in future interfaces.

## Atomic persistence

Add `agent_runs` through `AgentRunRepository`, which owns its SQL but shares the existing PaperStateStore connection and transaction. A `BEGIN IMMEDIATE` operation validates audit integrity, inserts the canonical run bytes/hash and appends the classification audit event together. The event includes the run hash, both traces, snapshot identity, routes and versions. Audit append failure rolls back the run. Concurrent identical insertion produces one result and one event; changed reuse of a run ID conflicts. Review trace IDs cannot be reused across runs.

Reads verify the audit chain, result byte hash, typed bundle invariants, canonical serialization, run/review identity and exact binding to the audit event. Updating the result and its local hash alone cannot evade the audit binding. A database administrator able to recompute all records and the audit chain can still fabricate evidence; this is not independent immutable or signed provenance.

The API snapshot archive is committed before classification and remains a separate transaction. A failed run transaction can leave an unused snapshot. Snapshot absence on later retrieval fails closed. Legacy classification audit events are not turned into invented full envelopes; only new runs are stored. A stored run proves generation and persistence, not that an HTTP client received or accepted it. Final expiry checks still occur after the transaction and can withhold an expired response while preserving its archival evidence.

## Bounds and limitations

Run storage defaults to 10,000 entries and at most 1 MB per serialized bundle. Capacity is enforced within the transaction and existing identical records remain idempotent at capacity. No automatic eviction or retention reset exists. Without AI_QUANT_PAPER_DB, storage is in memory. Production retention/export, access control, independent provenance, graph checkpoints, replay supervision, agent registry and 18 agent implementations remain open.

This is a P07 trace-persistence foundation. It does not implement LangGraph, Temporal, production model routing or broker permissions for the full runtime. All stored results remain advisory and cannot supply a kernel-issued risk approval.

## Verification and rollback

Tests cover restart retrieval, duplicate/conflicting runs, transactional audit rollback, concurrent capacity and identity races, malformed cross-envelope provenance, edited/rehashed results, missing snapshots, sanitized failures and archival expiry. Existing post-storage expiry regressions now target the atomic run transaction and retain the same rejection assertions.

The migration is additive. Rollback reverts API and persistence wiring while retaining `agent_runs` and the audit history. Older code ignores the table and reads classification events with their additional fields. Do not delete the table to perform rollback; retain evidence for later review.

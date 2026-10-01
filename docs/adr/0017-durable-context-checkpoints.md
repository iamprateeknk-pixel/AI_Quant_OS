# 0017: Context checkpoints committed with audited provenance

## Decision and contract

Persist ContextCheckpoint schema 1.0, containing the complete compiled context and the exact AgentManifest used for compilation. The key is the content-derived context_ref. Checkpoint validation binds agent/version, manifest hash, declared input order, source capability and dataset permissions. Context/source integrity is verified independently of wall-clock freshness so historical evidence remains readable without extending its validity.

Context compilation now commits the checkpoint and its audit entry in one PaperStateStore transaction. The existing creation response gains checkpoint_ref equal to context_ref. Final consumer admission and expiry checks still occur after storage; a context can remain archived even when the response is withheld because it expired during the transaction. Stored evidence proves compilation/persistence, not successful receipt by a client or agent activation.

Authenticated GET /api/v1/agents/contexts/{context_ref} returns PAPER mode, archival_only=true, the original checkpoint and a current time-window flag. Operator authorization is required for this archive because contexts may later contain research/evaluation evidence. Retrieval never fetches source data, calls a provider, changes lifecycle, invokes tools or creates orders. Consumers must separately recheck the current registry/task permissions with require_context_for_agent before using a current context. Expired context cannot be admitted after restart.

## Atomic storage and integrity

The additive context_checkpoints table stores the primary-key reference, canonical checkpoint bytes and SHA-256 hash. ContextCheckpointRepository owns its SQL while PaperStateStore owns the existing SQLite lock and BEGIN IMMEDIATE transaction. Validate the audit chain, insert the immutable checkpoint and append its audit event together. Audit failure rolls back the artifact. Concurrent identical insertion is idempotent and produces one event.

Reads verify the audit chain, checkpoint byte hash, supported schema, canonical encoding, reference/manifest/evidence binding, source validity at the original compilation time, and exact audit-event binding including checkpoint_hash. A valid audit chain with a wrong checkpoint binding is insufficient. These checks are local corruption detection, not signed source truth or protection against an administrator rewriting artifacts and the entire audit chain.

Default retention is bounded to 10,000 checkpoints and 64 KiB per checkpoint including manifest. Existing exact retries remain idempotent at capacity. New checkpoints fail closed at capacity; no automatic eviction, export or deletion occurs. Configured AI_QUANT_PAPER_DB retains artifacts across restart; otherwise they are in memory. Physical dataset isolation, operator-session policy, independent immutable storage and production retention remain open.

Initial manifests remain PLANNED. Persisting one does not mark its mission implemented. These are context evidence checkpoints; they are not LangGraph execution checkpoints, resumable task state or an implemented supervisor.

## Verification and rollback

Tests cover restart retrieval, expired-context admission rejection, exact manifest retention, immutable/idempotent inserts, concurrent duplicates/capacity, atomic audit rollback, manifest mismatch, rehashed noncanonical records, valid-chain/wrong-binding rejection, authenticated retrieval and sanitized corruption failures. The existing expiry-during-audit regression now targets the atomic checkpoint method and retains the same 409 assertion.

Migration is additive at startup. Rollback reverts API/repository wiring while retaining context_checkpoints and audit evidence; older code ignores the table. Preserve the database before any version rollback. Older random-key context audit events cannot be reconstructed into full checkpoints, and no fictional backfill is attempted.

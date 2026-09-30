# 0011: Immutable local world-state publication

## Decision and interface

Keep current market ingestion and the live advisory view in memory. Add `WorldStateStore` to preserve immutable advisory snapshots in the same configured SQLite database, or in a separate in-memory database when `AI_QUANT_PAPER_DB` is absent.

Authenticated `POST /api/v1/paper/world-state/publish` compiles the current server-owned paper evidence, stores it, and records a publication event in the verified audit history. It accepts no client snapshot fields. An empty request body or `{}` is accepted. Publishing an identical version is idempotent and returns `inserted=false`.

`GET /api/v1/world-state/archive/{world_state_version}` retrieves a verified snapshot with `archival_only=true` and a `current` flag computed from its original time window. Expiry is never extended. This flag describes that time window; it does not imply agreement with current market data. Historical retrieval cannot reload quotes or bars, call a provider, or authorize an order. Missing versions return 404; storage/integrity failures return sanitized 503 responses.

Classification stores its input snapshot before invoking the decision gateway. Storage failure prevents provider dispatch. The existing classification audit event includes `snapshot_ref`, allowing the exact advisory feature input to be retrieved after restart. This does not persist a complete decision envelope or reconstruct the original raw quotes/bars from their evidence hashes.

## Storage and integrity

The additive `world_state_snapshots` table has a primary-key version, canonical typed JSON and a SHA-256 byte hash. Inserts use `BEGIN IMMEDIATE`; conflicting reuse of a version is rejected, including an idempotent retry against a corrupted row. Reads verify the hash, typed schema, supported paper policy/source, aware timestamps, finite numeric evidence, version identity and canonical encoding. Expiry equal to creation is retained because the engine can compile evidence exactly at its source deadline; such an archive is never current.

Default capacity is 10,000 snapshots with at most 1 MB per serialized snapshot. Concurrent adapter instances sharing a database enforce the capacity transactionally. Existing identical versions remain readable and idempotent at capacity. There is no automatic deletion or capacity reset. New publication fails closed when full; retention/export governance remains to be implemented. Ordinary live-view reads do not write archives.

The archive byte hash is separate from the engine's feature-derived version. The store does not independently recompute all feature derivations from raw market evidence. A party able to rewrite the database and its hashes can fabricate internally consistent evidence. This is local corruption detection, not signed provenance or independent immutable storage. Publication and the audit event use separate transactions; a failed audit append can leave an unreferenced archive but cannot produce an accepted classification response. Distributed streaming/publication, raw evidence retrieval, production retention and access policy remain open.

## Verification and rollback

Tests cover restart retrieval, exact duplicate publication, conflicting versions, corrupted rows, rehashed invalid identities/contracts/extra fields, concurrent same-version inserts, capacity races, protected publication, unknown versions, historical expiry, audit references and storage failure before provider invocation. Current quotes remain empty after restart.

Rollback reverts API wiring and the adapter without deleting `world_state_snapshots`. The older application ignores this additive table. Preserve the database and audit history; no existing order, fill or risk schema is modified.

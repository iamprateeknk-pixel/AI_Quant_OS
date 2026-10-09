# ADR 0128 — Publish retained demo account observations behind the owner boundary

Status: implemented locally; frontend/independent/native acceptance pending.

## Decision

Publish a bounded latest account/position/order projection after each completed
demo service tick, with original source expiry, receipt/scope and service run UUID.
Serve it from a fixed server-owned file under existing operator authentication.
Keep native/history/freshness attestation unverified, non-atomic source semantics
and order/portfolio authority false. Do not import external amounts/positions into
the paper risk or execution path.

Share atomic bounded JSON I/O with heartbeat publication, preserving its wire
model and behavior. Stop further service ticks if portfolio publication or source
verification fails; retain any already committed controller/projection state.

## Consequences

Account observations reach the protected portfolio read without credentials in
the API or renewed native leases. Expired saved state remains descriptive. Files
are individually atomic rather than an atomic pair. Frontend display, complete
native reconciliation and deterministic external capital-control acceptance stay
separate requirements. Rollback keeps all source journals/records; existing
schemas, domain contracts and journal formats remain compatible.

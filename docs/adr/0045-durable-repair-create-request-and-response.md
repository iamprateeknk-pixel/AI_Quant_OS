# 0045 — Retain exact creation request and response before admission

Status: Accepted for local parent creation provenance and cleanup identity.

## Decision

Add immutable schema 1.2 creation intent and acknowledgement to the existing
repair journal. Commit intent after the exact ownership lease and before the
single RPC. Bind the ordered arguments and canonical lease. Commit a full-ID
response before metadata admission. Recheck the request before restart daemon
access and require acknowledged IDs for removal. Creation uncertainty never
triggers resend or absence-based lease release. Reserve five events per owner.

## Consequences

Real storage and substituted daemon cases prove pre-RPC ordering, reopening,
failure preservation, no consumer admission on failed acknowledgement persistence,
request/image/ID binding and cleanup capacity. Creation acknowledgements are
response records, not confinement or behavioral certificates. Existing removal
and terminal bytes remain unchanged; old readers reject the new schema. Preserve
all evidence for rollback and review policy migrations. Actual daemon behavior,
power-loss, staging before lease publication, ambiguous RPC completion fencing,
isolated verification and the broader recovery/release stages remain pending.
See `../REPAIR_CREATE_JOURNAL_HANDOFF.md`.

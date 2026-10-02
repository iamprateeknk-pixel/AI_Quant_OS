# 0043 — Persist exact repair removal intent and acknowledgement

Status: Accepted for intact retained-job acknowledgement recovery.

## Decision

Under local root ownership, commit immutable owner/container/lease-bound intent
before removal and an acknowledgement after the exact successful Docker response.
Use a separate bounded SQLite audit journal with synchronous FULL commits. Verify
the existing chain and head without migration or rebaselining. Reserve capacity
for every admitted acknowledgement. Use an exact committed acknowledgement to
finish interrupted lease release without daemon access.

## Consequences

Real storage and fault-injected daemon tests prove reopening, exact binding,
idempotence, tamper refusal and recovery after interrupted lease release. Missing
acknowledgements preserve uncertainty, even when the container appears absent.
The intact job and unchanged receipt remain required. Partial input deletion,
missing receipts, ambiguous create/removal completion, pre-create capacity gates,
rotation, live Docker and power-loss behavior remain open. This is local cleanup
evidence, not a behavioral certificate or promotion authorization.
See `../REPAIR_REMOVAL_JOURNAL_HANDOFF.md` for limits and rollback.

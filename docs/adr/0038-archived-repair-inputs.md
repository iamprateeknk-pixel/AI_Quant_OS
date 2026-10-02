# 0038 — Bind archived graph inputs to repair candidates

Status: Accepted for archival input preparation; execution pending.

## Decision

Read the existing graph ledger with SQLite-enforced read-only access. Require its
full audited record and exact canonical receipt to match the candidate's recorded
health incident. Bind the record bytes to the candidate source snapshot and retain
historical expiry. Prepare the record as a separate file in optional job schema
1.1; preserve job schema 1.0 when no replay is supplied.

## Consequences

Altered receipts, graph corruption, missing archives and mismatched snapshots fail
closed. Summary commands reveal identities and hashes without captured payloads.
No candidate execution or eligibility restoration occurs. Archival inline inputs
are incomplete evidence for external tools/datasets and deployed source identity;
these gaps must close before baseline reproduction or a repair certificate can be
claimed. See `../REPAIR_REPLAY_HANDOFF.md` for evidence and remaining requirements.

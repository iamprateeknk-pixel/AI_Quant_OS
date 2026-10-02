# 0044 — Resume filesystem cleanup from exact removal evidence

Status: Accepted for cooperating local cleanup after committed removal.

## Decision

Reconstruct released lease bytes from an exact audited removal acknowledgement
against independent source/image/suite/replay pins. After that acknowledgement,
allow missing inputs for deletion only, checking every survivor and the original
root identity. Recheck under local root ownership before cleanup. Require current
receipt bytes when a receipt still exists. Commit additive schema 1.1 `CLEANED`
only after job and lease absence. Reject path reappearance after terminal cleanup.
Block new staging on unfinished journal entries and leftover job paths. Reserve
three events per admitted owner and check capacity before create/lease publication.

## Consequences

Actual Git/SQLite/files and fault-injected daemon cases prove recovery after
release, partial deletion and deletion before terminal commit, safe duplicate
delivery, survivor tamper refusal and conservative terminal persistence failure.
No missing source inventory is accepted for execution. Existing schema 1.0 intent
and acknowledgement events retain their bytes; older readers reject new terminal
records and require a reviewed rollback procedure. Ambiguous create/RPC completion,
power-loss durability, live Docker confinement, journal rotation and broader
behavioral/Temporal/shadow/canary/release work remain open.
See `../REPAIR_REMOVAL_JOURNAL_HANDOFF.md` for evidence and limitations.

# ADR 0124 — Independently pinned offline demo sync checkpoints

Status: implemented locally; independent/native acceptance pending.

## Decision

Preserve the full original gateway, projection and controller audit histories in
a bounded portable archive under the original connection/schedule policy. Hold
the CLI gate and all three SQLite writer locks while backing up committed state.
Validate copied journals, retain hashes and require an independently supplied
archive pin before restoring into a new directory. Never reset existing state,
adopt uncertain ownership or dispatch native work during export/restore.

Publish restored files under a durable incomplete marker and the existing CLI
gate. Retain partial publication on failure and refuse normal service startup
while the marker exists. Existing destinations/archives are never overwritten.

## Consequences

This supplies artifact-bound same-policy migration and recovery preparation.
It does not compact capped histories, fence two active directories, resolve
native custody or establish broker completeness. Filesystem integrity and
directory power-loss durability are separate acceptance concerns. Original
configuration and archive hash must be retained outside the artifact. Prior
journal and wire formats remain unchanged; rollback to older service code
requires stopped launchers because those versions lack the incomplete guard.

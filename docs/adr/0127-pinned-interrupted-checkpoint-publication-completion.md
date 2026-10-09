# ADR 0127 — Finish an originally bound checkpoint publication explicitly

Status: implemented locally; independent/native acceptance pending.

## Decision

Replace new restore's bare incomplete marker with a versioned intent binding
original archive/policy, prepared journal bytes, UUID and resolved destination
identity. Require independently supplied archive and intent hashes to finish
only missing files under the existing CLI gate. Verify existing/final bytes and
unchanged intent before releasing the startup guard. Retain changed/unknown/shared
files and every failed prefix without overwrite, reset or automatic retry.

## Consequences

An operator can finish a known publication, including failed final marker removal,
without redispatching native work or changing archived audits. Legacy unbound
markers, relocation, filesystem uncertainty and native custody remain withheld.
Copying grants no terminal ownership, trading/portfolio authority, compaction or
broker acceptance. Existing schemas/journals are unchanged. Rollback preserves
all artifacts and keeps launchers stopped on partial state.

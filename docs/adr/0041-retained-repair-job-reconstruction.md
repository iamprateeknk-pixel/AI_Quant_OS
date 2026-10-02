# 0041 — Reconstruct retained jobs before restart cleanup

Status: Accepted for present owned-container cleanup; wider recovery pending.

## Decision

Derive expected input bytes from independent source/image/suite/replay pins, not
from mutable retained requests. Require exact canonical receipt binding and full
file/root admission before rebuilding pending ownership in memory. Validate the
receipt again at cleanup admission to reject changes after reconstruction.

Share exact owned-container removal between ordinary and restart cleanup. Release
ownership and delete inputs only after a confirmed immutable-ID removal response;
print success only after job cleanup finishes. Treat failed inspection/absence as
unresolved, not permission to delete inputs.

## Consequences

Existing receipt schema 1.0 can support verified present-container recovery.
Unknown absence, active-owner concurrency, crash durability and transactional
cleanup still need lifecycle journaling/fencing. Fault-injection and real
filesystem/CLI checks are not live kernel or Docker verification. See
`../REPAIR_CLEANUP_HANDOFF.md` for evidence, limitations and rollback handling.

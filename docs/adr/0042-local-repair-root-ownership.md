# 0042 — Serialize cooperating repair writers with local kernel ownership

Status: Accepted for local job-root ownership; distributed fencing pending.

## Decision

Use a persistent regular lock file and a non-inheritable OS-held exclusive lock.
Acquire nonblocking and reject conflicts. Hold ownership over staging, consumer
and container lifetime, and cleanup. Retained reconstruction and restart cleanup
also acquire ownership; cleanup revalidates inputs after reacquisition.

## Consequences

Actual trusted subprocess tests prove local Windows exclusion and release after
owner death. Persistent receipts continue to block blind staging after death.
The lock coordinates cooperating local writers, not hostile admins or distributed
filesystems; it does not terminate containers or resolve uncertain absence/RPCs.
Preserve the stable lock inode and stop pre-protocol owners before recovery.
See `../REPAIR_ROOT_OWNERSHIP_HANDOFF.md` for verification and rollback limits.

# Durable repair removal acknowledgements

The trusted parent now records removal intent before contacting Docker and records
`REMOVED` only after an exact successful acknowledgement for the inspected full
container ID. Both events bind the owner, selected image and canonical job lease,
including root identity and source, suite and request hashes. Ordinary admission
cleanup and restart cleanup share this protocol under local job-root ownership.

## Recovery boundary

If acknowledgement committed but lease release was interrupted, restart cleanup
can use that exact persisted acknowledgement without contacting Docker. Recovery
requires independent source/image/suite/replay pins. An existing receipt must be
unchanged. If it was already released, its canonical bytes come from the verified
acknowledgement, never from container absence or untrusted client metadata. Every
surviving input must still have its expected bytes, type and single-link identity,
and a surviving job root must have its original device/inode. Missing inputs are
accepted only for cleanup after confirmed removal. It then releases any remaining
lease and deletes the verified remainder. An intent alone, missing container,
failed acknowledgement commit, changed receipt or journal corruption cannot
establish removal.

Recovery covers interruption before lease release, after release, during input
deletion, and after deletion before the terminal commit. A schema 1.1 `CLEANED`
event commits only after both the job and lease are absent. Duplicate terminal
delivery is idempotent. Reappearing paths at an already-cleaned identity reject
recovery; the old record cannot restore deletion authority. Ambiguous creation
and removal whose RPC succeeded but whose acknowledgement could not commit remain
uncertain and need further lifecycle work.

## Storage and admission

`repair-removals.sqlite3` lives in the job root, outside candidate mounts. Schema
1.0 intent/removal and additive schema 1.1 terminal events are immutable and
idempotent by owner/state, ordered `INTENT`, `REMOVED`, `CLEANED`. The local audit
sequence, hash chain and committed head are verified before reads and writes.
Existing databases are never rebaselined;
missing audit metadata rejects admission. A new database uses SQLite synchronous
FULL commits. Existing files must be regular, single-link, bounded and free of
Windows reparse points. Events contain lease metadata, not source/input payloads.

The subsequent creation increment admits at most 1,000 owners and 5,000 events,
reserving two additional creation events alongside removal and terminal cleanup.
Existing removal/terminal bytes remain unchanged. Every admitted owner reserves
room for acknowledgement and terminal cleanup, including at capacity. Container
admission checks capacity under root ownership before lease publication or daemon
calls. New staging refuses unfinished journal owners and leftover job paths even
when leases have already been released. Rotation/archival remains unfinished;
a full journal rejects new containers while admitted owners can finish cleanup.
An orphan input tree from interruption before lease publication still blocks new
staging and requires reviewed reconciliation; it has no removal acknowledgement
and is not adopted or deleted automatically by this protocol.

## Evidence and remaining work

The initial six tests exercise real SQLite, Git and retained files with a substituted
Docker boundary: exact binding, reopening and duplicate delivery, missing head,
body corruption, acknowledgement room at capacity, interrupted lease release,
and failed acknowledgement persistence followed by apparent container absence.
The last two use separate interruption cases in the cleanup tests. Recovery from
a committed acknowledgement makes zero daemon calls. Full results are recorded
in `BUILD_STATUS.md`. No candidate/verifier program or container was started.

Twelve subsequent cases cover all three filesystem interruption points, duplicate
completed delivery, altered surviving bytes/extra files/directories/hard links/
root/receipt refusal, failed terminal persistence blocking new staging until
reconciliation, terminal presence/reappearance refusal, reserved terminal capacity
and pre-create refusal at journal capacity. Initial tests reproduced missing-lease
recovery failure, terminal records accepting reappearing receipts and creation at
full capacity; each was corrected before fresh verification. Daemon responses are
substituted; these checks prove local control flow and real storage/file behavior.

The trusted local filesystem, cooperating root-lock callers and daemon are part
of the boundary. A local hash chain is not an externally signed behavioral
certificate or protection against a hostile administrator rewriting all evidence.
Power-loss, actual Docker/daemon restart and Linux hosted verification remain
unverified. Definitive ambiguous create/removal completion reconciliation,
reviewed offline execution image and worker, live kernel confinement, independent
behavioral checks, Temporal repair stages, shadow/canary and full release gates
remain open. Quarantine and promotion authority are unchanged.

For rollback, stop active owners and preserve the journal, lock file, pending
receipts and inputs. The original reader rejects schema 1.1 terminal events; reverting
requires retaining evidence and a reviewed recovery/migration procedure. It cannot
use these journals as valid removal evidence or safely resume partial cleanup.
Never treat a deleted/missing journal or stale-looking receipt as removal proof.

Schema 1.2 creation intent/response records now bind exact arguments and acknowledged
container IDs before admission and later removal. Absence remains unresolved;
see `REPAIR_CREATE_JOURNAL_HANDOFF.md` and ADR 0045 for ordering, migration and
verification limits.

# Durable repair removal acknowledgements

The trusted parent now records removal intent before contacting Docker and records
`REMOVED` only after an exact successful acknowledgement for the inspected full
container ID. Both events bind the owner, selected image and canonical job lease,
including root identity and source, suite and request hashes. Ordinary admission
cleanup and restart cleanup share this protocol under local job-root ownership.

## Recovery boundary

If acknowledgement committed but lease release was interrupted, restart cleanup
can use that exact persisted acknowledgement without contacting Docker. It still
requires independent source/image/suite/replay pins, the intact retained job and
the unchanged current ownership receipt. It then releases the lease and deletes
the verified inputs. An intent alone, missing container, failed acknowledgement
commit, changed receipt or journal corruption cannot establish removal.

This closes the interruption between acknowledgement persistence and lease
release. It does not recover a missing lease after release, partially deleted
inputs, ambiguous creation, or removal whose RPC succeeded but whose acknowledgement
could not commit. Those cases retain uncertainty and need further lifecycle work.

## Storage and admission

`repair-removals.sqlite3` lives in the job root, outside candidate mounts. Schema
1.0 events are immutable and idempotent by owner/state, with exact intent-before-
acknowledgement ordering. The local audit sequence, hash chain and committed head
are verified before reads and writes. Existing databases are never rebaselined;
missing audit metadata rejects admission. A new database uses SQLite synchronous
FULL commits. Existing files must be regular, single-link, bounded and free of
Windows reparse points. Events contain lease metadata, not source/input payloads.

At most 1,000 owners and 2,000 events are admitted. Every admitted intent reserves
room for its acknowledgement, including at capacity. Rotation/archival and
capacity admission before container creation remain unfinished; a full journal
fails cleanup conservatively and preserves pending ownership and inputs.

## Evidence and remaining work

Six new tests exercise real SQLite, Git and retained files with a substituted
Docker boundary: exact binding, reopening and duplicate delivery, missing head,
body corruption, acknowledgement room at capacity, interrupted lease release,
and failed acknowledgement persistence followed by apparent container absence.
The last two use separate interruption cases in the cleanup tests. Recovery from
a committed acknowledgement makes zero daemon calls. Full results are recorded
in `BUILD_STATUS.md`. No candidate/verifier program or container was started.

The trusted local filesystem, cooperating root-lock callers and daemon are part
of the boundary. A local hash chain is not an externally signed behavioral
certificate or protection against a hostile administrator rewriting all evidence.
Power-loss, actual Docker/daemon restart and Linux hosted verification remain
unverified. Create/terminal lifecycle journaling, interrupted filesystem deletion,
reviewed offline execution image and worker, live kernel confinement, independent
behavioral checks, Temporal repair stages, shadow/canary and full release gates
remain open. Quarantine and promotion authority are unchanged.

For rollback, stop active owners and preserve the journal, lock file, pending
receipts and inputs. Older code cannot use persisted acknowledgements for recovery.
Never treat a deleted/missing journal or stale-looking receipt as removal proof.

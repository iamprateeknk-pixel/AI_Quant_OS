# Local repair job-root ownership

`repair_root_lock` gives one cooperating local parent exclusive ownership of an
existing job root. It uses a nonblocking Windows byte-range lock or POSIX file
lock. Conflict and unavailable locking fail before staging or daemon mutation.
Ownership follows the open kernel handle; file presence and recorded PIDs are
not used to infer whether an owner is alive.

The persistent `.repair-owner.lock` file is outside every candidate job mount.
It is a regular single-link file containing one zero byte; invalid types, reparse
points, hard links, size or contents are refused. Opened file and root identities
are checked. The descriptor is explicitly non-inheritable. Initialization occurs
under the acquired lock. The file is never unlinked by this protocol, so every
cooperating writer locks the same filesystem object.

## Lifetimes

Job preparation acquires ownership before unresolved-lease admission and staging,
and holds it through the entire consumer/container lifetime and final file
cleanup or retained-job failure. Retained job reconstruction acquires ownership
while reading/admitting its files. Cleanup separately reacquires ownership and
rechecks inputs and the current receipt before daemon access, holding the lock
through removal, lease release and input deletion. A competing admission cannot
stage beside an existing pending lease after reconstruction releases its lock.

The existing `admit` and `cleanup` commands use these library entry points.
Conflict returns rejection without a success receipt. Existing roots may gain
the persistent one-byte lock file; retained job contents and receipt schemas are
unchanged. Reconstruction still reads candidate inputs only, but it now creates
this root-control file when missing. Different roots remain independent.

## Evidence and limits

Eight new tests verify actual local subprocess exclusion and successor ownership
after forced termination of the trusted fixture owner, same-process exclusion,
independent roots, full staged-job consumer lifetime, invalid lock identity and
contents, missing-root refusal, exception release, and reconstruction/cleanup
refusal before daemon calls while the root has a live owner. The child process
executes only trusted locking code; candidate/verifier code is never run. These
checks were exercised on this Windows host; Linux implementation/CI is configured
but hosted verification remains pending. Full results are in `BUILD_STATUS.md`.

This is a cooperative local filesystem protocol. All writers must use it and the
lock file must remain in place while roots are active. It does not defend against
a hostile administrator changing paths/files, support network/distributed
filesystems, fence an untrusted daemon, or prove that a container stopped when
its parent died. Old processes predating this protocol must be stopped before
using it for recovery. The low-level created-container helper expects a job
provided within its preparation lifetime, not a manually constructed object.

Parent death releases the OS lock while persistent container receipts still block
new staging. Exact present-container recovery can then proceed; unknown absence
and ambiguous RPC completion remain unresolved. Durable lifecycle journaling,
idempotent interrupted cleanup, power-loss/daemon-restart evidence, live kernel
confinement and actual behavioral verification remain required, together with
Temporal repair stages, certificates, shadow/canary and release gates.

Rollback does not change data schemas. Preserve lock files, pending receipts and
input trees and stop active owners before reverting; a stale-looking lock file
is not permission to delete it or its associated job.

References: [Python Windows locking](https://docs.python.org/3.11/library/msvcrt.html)
and [Python POSIX file locking](https://docs.python.org/3.11/library/fcntl.html)
describe the platform calls and their nonblocking modes used by this adapter.

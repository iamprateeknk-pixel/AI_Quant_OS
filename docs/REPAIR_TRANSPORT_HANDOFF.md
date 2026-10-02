# Bounded parent-side repair commands

The Docker prerequisite reader now uses `repair_transport.bounded_command`.
This closes an unbounded parent-memory path in the prior `capture_output` reader;
it is a transport prerequisite for the future execution controller.

Two pipe readers drain binary stdout/stderr concurrently under one shared byte
limit. At most the declared limit is retained, with two fixed 4 KiB read chunks.
Output beyond the combined cap is rejected, including stderr. The parent kills
and waits for its owned child on timeout, overflow or interruption; pipe readers
must finish before returning. Child stdin is closed, file descriptors are not
inherited, and the explicitly supplied environment replaces ambient variables.

Limits are validated before launch: finite positive deadline at most 60 seconds,
integer output cap from 1 byte through 1 MiB. Actual Docker reads use 10 seconds
and 64 KiB combined, a fixed local daemon endpoint and the existing minimal
environment allowlist. Nonzero commands, invalid JSON and transport failures
cannot grant prerequisite availability.

Thirteen added checks launch only trusted short fixture programs. They cover
binary byte preservation, nonzero status, absent ambient secrets, infinite stdout
and stderr termination, combined exact/overflow limits, timeout preventing a late
file write, invalid pre-launch limits and fixed local Docker endpoint/environment
binding. The actual existing read-only Docker probe also passes its unavailable
runtime check. Full results are recorded in `BUILD_STATUS.md`; CI includes these
tests on Windows/Linux, but hosted results are unverified.

## Remaining execution boundary

This function is for trusted parent-side commands. It is not an OS sandbox or a
process-tree supervisor and must never run candidate code on the host. It reaps
the directly owned command; inherited descendant pipes cause a visible cleanup
failure, not readiness. Process creation itself may exceed the deadline on some
platforms. Cleanup has a bounded additional wait after termination.

Killing the Docker client does not stop its container. The execution controller
still needs unique owner identity, ambiguous-create recovery, exact created
container admission, direct kernel confinement proof, explicit owned container
termination/removal and job cleanup only after confirmed termination. Offline
toolchain/suite ownership, historical source/transcript closure, behavioral
verification and recovery/release gates remain open. No container was created or
started and no candidate/verifier suite was executed in this increment.

No data or schema migration is introduced. Rollback restores the old parent
reader; retained incidents and candidates remain untouched.

References: [Python subprocess documentation](https://docs.python.org/3.11/library/subprocess.html)
describes pipe buffering, explicit environments and process creation/timeout
limits. [Docker create documentation](https://docs.docker.com/reference/cli/docker/container/create/)
documents the separate container lifecycle that the execution controller must own.

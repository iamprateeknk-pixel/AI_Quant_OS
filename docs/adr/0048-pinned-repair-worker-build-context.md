# ADR 0048: Pinned dependency-only repair worker build inputs

Date: 2026-10-02
Status: Accepted for input preparation; image/execution acceptance pending

## Decision

Pin the official CPython 3.11.17 Bookworm slim Linux/amd64 manifest and retain the
index/manifest/config bytes establishing that selection. Recheck Linux wheel
dependency markers at this patch version. Keep the 51-wheel application lock
separate from the base image's inherited toolchain.

Prepare an explicit new build context through a bounded metadata/file adapter,
with wheel hashes, lock agreement, base binding and an allowlisted copy. Reject
existing/overlapping destinations and ambiguous metadata; preserve partial output
on write failure. The receipt is an input inventory only and grants no authority.

Use a two-stage dependency-only Dockerfile with the same immutable base in both
stages, hash-enforced wheel-only installation with its network disabled, no
compilation and no project/candidate/verifier source. Never run target bytes on the
host. Future actual image building and confined execution require their own gates.

## Consequences

Inputs can be prepared while the daemon is unavailable. Public metadata hash
agreement and version checks do not establish image-layer integrity, security
acceptance, runtime compatibility, signatures or behavioral certificates. The
preparer trusts operator recipes; it is not a Dockerfile policy analyzer or a
hostile-admin filesystem boundary. A pin opts out of automatic base changes; review
updates explicitly. Installed worker ID, Linux/kernel/UID evidence and candidate
execution remain pending. Rollback removes the new build-input feature/recipe;
it does not clear quarantines, change candidates or affect retained job ownership.

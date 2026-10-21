# Repair worker input preparation

## Current runtime update (2026-10-14)

Docker Desktop is running again. The pinned input-only image passed the live
metadata-only dependency probe and five fixed kernel/resource scenarios; all
owned probe containers were removed. This does not run a proposed repair or
verifier suite. Retained evidence: `data-local/dependency-runtime-0081-continuation`,
`data-local/worker-enforcement-0083-continuation`, and
`docs/evaluations/repair-runtime-live-0160.json`.

Staging now applies explicit 0755 directory and 0644 file modes. This closes the
code-level dependence on a permissive POSIX umask. Complete and remaining inventory
checks require those modes on POSIX. Parent control files remain outside the
candidate mount. This does not replace read-only bind enforcement or prove how
Windows ACLs are translated by Docker Desktop.

The Windows regression first failed because intermediate directories had no
explicit permission operation. It now verifies all mounted directories/files
receive their declared modes. Four tests require actual POSIX semantics: one
checks umask 077 with private control/readable inputs, and three refuse changed
root/directory/file modes. They are configured in the existing Linux CI job but
skip locally; Linux behavior and actual non-root container reads remain unverified.

The offline worker profile at `infra/repair-worker` pins eight current direct
runtime/test versions and 51 target packages. All 52 downloaded archive hashes and
sizes matched PyPI release metadata, and target marker/version/extras closure was
checked without importing them. An actual offline, hash-enforced pip download
selected all 51 lock entries. Bytes remain in ignored local directories. The
manifest retains each target wheel's hash/size/tags, supplier reference and host
version for review. Fresh transitive versions still require Linux behavior checks.

At the time of the original input-preparation run, the read-only Docker probe
reported `ENGINE_UNAVAILABLE`. The later current-runtime update above records the
live metadata and fixed enforcement probes. No proposed repair source or repair
verifier suite has run, no certificate was emitted, quarantine was not released,
and promotion remains disabled. Dependency inputs alone are not an admitted
toolchain. Reviewed image/toolchain acceptance, isolated behavioral verification,
Temporal repair stages, shadow/canary and the broader full-build/release
requirements remain open. Full counts are in `BUILD_STATUS.md`.

Before rollback, preserve pending receipts, journal and inputs. Older POSIX jobs
with restrictive modes may fail new admission and need reviewed reconciliation.
Do not weaken read-only mounts or change source bytes to work around readability.
Preserve dependency locks, inventory and downloaded bytes; the profile has not
modified the current application environment.

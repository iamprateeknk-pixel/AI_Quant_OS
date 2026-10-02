# 0037 — Complete committed source snapshots for repair jobs

Status: Accepted for trusted-parent input preparation; candidate execution pending.

## Decision

Read the complete declared package from the candidate commit, not only patched
files or a mutable worktree. Bind unchanged dependencies, exact bytes, inventory,
candidate/baseline, proposed manifest and original health incident in a deterministic
snapshot identity. Reject irregular/undeclared package data and keep immutable
source values with copied metadata views.

Prepare only a fresh temporary owned job containing exact source, independently
selected pinned suite bytes and a bounded versioned request. Check file/root
identity, reparse points, hard links and exact content before a future read-only
mount. Clean up generated inputs on consumer exit without executing candidate or
suite Python. Use actual path-based Windows file identity for admission.

## Consequences

Actual Git/SQLite/filesystem/CLI tests cover source and input preparation. They do
not satisfy independent behavioral verification or prove OS confinement. No registry
version, quarantine, safety state or capital authority changes. Health/candidate
storage does not migrate; snapshot/job schema is 1.0 and no job journal is installed.

The execution owner must select a reviewed suite, enforce and verify isolation,
own/drain container lifetime, bound output/time and check archived context/dataset
closure for actual incident replay. Host-owned input preparation assumes a trusted
parent filesystem; no hostile-host or live sandbox proof is claimed. The local
runtime fault remains and source must not run on the host as fallback. A16,
certificates, repair workflows, shadow/canary and recovery/rollback gates remain open.

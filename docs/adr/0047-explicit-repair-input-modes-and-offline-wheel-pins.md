# 0047 — Prepare readable inputs and retained offline worker dependencies

Status: Accepted for input preparation; Linux and image admission unverified.

## Decision

Set job and source directories to 0755 and copied files to 0644 explicitly, after
creation. On POSIX, reject differing mounted-input modes during complete/remaining
inventory verification. Keep host control receipts outside the candidate mount;
the enforced read-only bind remains the target write-isolation boundary.

Retain a separate CPython 3.11/Linux x86-64 dependency profile with eight pinned
roots, exact target wheel hashes, published PyPI size/hash checks and metadata
closure verification. Keep downloaded Linux native wheels out of the Windows
runtime. Offline pip hash enforcement verifies artifact availability only.

## Consequences

Windows checks demonstrate explicit permission-setting operations for the complete
input tree. Four real POSIX checks cover restrictive umask and changed root/directory/
file modes, but skip on Windows and remain unverified until Linux execution.
Windows/Docker Desktop ACL translation and actual UID 65534 readability still need
live probes. Fresh transitive wheel versions can differ from tested host versions;
real Linux installation/import/regression and independent dependency review remain
required. No image or candidate program was built/started/executed. Base/runtime
pinning, worker/controller, behavioral certificates and broader release gates stay
open. See `../REPAIR_WORKER_INPUTS_HANDOFF.md` and `../../infra/repair-worker/README.md`.

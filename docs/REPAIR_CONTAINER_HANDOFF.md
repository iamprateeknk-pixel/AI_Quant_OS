# Owned repair container admission and cleanup

This increment owns the create/inspect/remove lifecycle. It does not start
containers, run a candidate/verifier suite, test live kernel enforcement, issue
certificates or release quarantine. `CreatedRepairContainer` conveys admission
identity only; promotion remains disabled.

## Implemented path

`prepared_repair_container(policy, job)` requires the exact prepared job type,
matching selected suite hash, intact input files and local runtime/image
prerequisites. It fixes all existing confinement options, disables image pulls,
health checks and automatic restarts, and supplies only the exact read-only job
mount. Unsafe CSV mount path characters are rejected before creation.

Before creation, a new opaque owner identity and deterministic container name are
published in an exclusively created, flushed/fsynced schema 1.0 lease receipt
beside the job. The receipt records the image, job directory identity and
snapshot/suite/request hashes. It contains no source bytes or captured inputs and
is outside the candidate's mount. Only one container may hold a job at a time.

The successful create response must be a full immutable ID. Inspection checks
that ID, exact name, owner label and image, then the existing full confinement
metadata policy. Inputs are checked again before returning admission identity.
There is no start/run command in this controller.

On normal exit, consumer failure or admission failure, cleanup inspects ownership
again and removes only the exact immutable ID, using force plus removal of its
associated anonymous volumes. It never prunes the daemon, deletes foreign
containers or removes named volumes. A successful exact-ID removal response is
required before the lease is released and job inputs are deleted. Job deletion
also checks the original directory identity and resolved parent boundary.

An interrupted/ambiguous create is not retried. The prepublished unique name is
inspected only to locate an exact matching owned container for cleanup. Failed
inspection or apparent absence cannot prove no delayed create, so the job and
lease remain. Ambiguous removal, changed ownership or changed lease bytes also
retain inputs. A later staging request in that job root refuses an unresolved
lease instead of accumulating more retained source trees.

```powershell
ai-quant-repair --repository D:/AI_Quant_OS --health-store <health.sqlite3> admit <candidate-id> --image-id sha256:<owned-local-image> --suite-sha256 <reviewed-suite-hash> --suite-file <reviewed-suite.py>
```

Optional `--graph-store` includes the verified archived invocation record. This
command prints `REMOVED_AFTER_ADMISSION` only after both contexts have exited
successfully, with `execution_verified: false` and promotion disabled. Failure
returns no success receipt and directs the operator to retained leases.

## Verification and remaining work

Twelve new cases combine real Git/SQLite/source jobs with a fault-injected Docker
command boundary, plus an actual subprocess command using the unavailable local
daemon/missing pinned image. They check command constraints, admission/removal
order, consumer failure cleanup, ambiguous-create cleanup, weakened confinement
refusal, ambiguous absence/removal and foreign ownership retention, image-bearing
receipts, subsequent staging refusal, a second owner refusal, summary-only command
output and actual unavailable-runtime cleanup. These are lifecycle control-flow
checks, not actual Docker creation or isolation evidence. Full results are in
`BUILD_STATUS.md`; Windows/Linux CI is configured but hosted execution unverified.

The local engine remains unavailable. A usable reviewed offline image/toolchain,
direct kernel probes, a reviewed suite, non-root file access, historical deployed
source/tool transcript closure and actual isolated behavioral checks remain
required before any start path is implemented. Repair certificates, Temporal
repair stages, shadow/canary, incident closure and gated recovery/rollback remain
open.

Parent filesystem and local daemon are trusted. The receipt is an ownership aid,
not a signed audit journal or a lease-recovery service. Directory fsync, sudden
power loss, parent process death, daemon restart and volume behavior have not
been verified live. A process crash leaves generated jobs without an automatic
temporary-directory finalizer, but restart reconciliation/manual cleanup still
requires implementation. Cross-process job-root leases and races are not solved
by the single-job lock or pre-staging receipt check. Do not remove retained inputs
until independent ownership and container termination are confirmed.

Existing health, candidate, graph and job-request schemas remain unchanged. For
rollback, preserve unresolved ownership receipts and input trees; reverting this
layer does not prove their containers absent or make their deletion safe.

References: [Docker create](https://docs.docker.com/reference/cli/docker/container/create/)
documents creation without starting and possible volume initialization;
[Docker remove](https://docs.docker.com/reference/cli/docker/container/rm/)
documents force removal and associated anonymous-volume cleanup.

# Reconstruct and reconcile retained repair jobs

This increment handles a retained job whose exact owned container is still
present after parent restart. It validates inputs before daemon access, confirms
removal before deleting the job, and never starts a container or candidate.

## Implemented boundary

`load_retained_repair_job` rebuilds expected request and file bytes from the exact
committed source snapshot, independently supplied verifier bytes/hash, selected
image pin and optional original archived replay. It opens only an existing root
and bounded regular receipt file. Reparse points, hard links, outside-root
receipts and invalid names are rejected. Descriptor identity, type and bounded
bytes are checked, followed by canonical exact receipt reconstruction and full
job-file/root identity admission. This reader creates or changes no files.

The reconstructed object retains the original pending owner/image. Cleanup checks
all job inputs and the current receipt again before Docker access; a receipt
changed after reconstruction is rejected before any inspection/removal command.
Both normal and restart cleanup share exact owner/name/image/immutable-ID removal
logic. Recovery does not require image/runtime execution eligibility merely to
remove an exactly owned container.

After the exact removal response, the lease is released and the identity-checked
input tree is deleted. A success summary is returned only after those operations.
Missing/unavailable/foreign container metadata, uncertain removal, changed source
or ownership receipts leave the job and receipt in place.

```powershell
ai-quant-repair --repository D:/AI_Quant_OS --health-store <health.sqlite3> cleanup <candidate-id> --lease <retained.lease.json> --image-id sha256:<original-image> --suite-sha256 <original-suite-hash> --suite-file <original-suite.py>
```

Supply the same `--root` used for admission when it was customized. If the original
job carried an archived invocation record, also provide its original
`--graph-store`. The command independently retrieves the candidate snapshot;
incorrect pins or a missing original replay cannot reconstruct the request hash.
Output state is `REMOVED_AFTER_RESTART_RECONCILIATION`, with execution unverified
and promotion disabled. No source bytes or archived payloads appear in output.

## Evidence and limits

Fourteen new tests use real retained Git/SQLite/source jobs, actual filesystem
admission and a fault-injected Docker boundary. They verify unchanged read-only
reconstruction, confirmed removal before file deletion, unknown absence/foreign
ownership/uncertain removal retention, changed source/request/image/root/authority
and noncanonical receipt refusal before daemon calls, hard-linked/outside/missing
root refusal, receipt changes after reconstruction, command success after cleanup,
and a fresh CLI subprocess preserving reconstructed inputs when the local daemon
is unavailable. The successful cleanup path is not live Docker evidence. Full
verification is recorded in `BUILD_STATUS.md`; hosted CI is unverified.

Schema 1.0 ownership receipts remain unchanged. Present-container recovery is
implemented; apparent absence cannot prove a delayed create absent or an ambiguous
remove complete. Those states remain unresolved rather than being automatically
cleared. Owner liveness, concurrent cleanup/admission, cross-process fencing,
parent death/power-loss durability, daemon restart, live volume behavior and
idempotent recovery after a successful removal followed by failed file cleanup
still require stronger lifecycle journaling and verification. Container removal,
lease release and filesystem deletion are not one atomic transaction. Do not run
recovery concurrently with an active owner; this boundary assumes one trusted
operator/parent and trusted local filesystem/daemon.

The local engine remains unavailable. Live kernel probes, reviewed offline
toolchain/suite, historical deployed-source/tool transcript closure, actual
isolated behavioral verification, certificates, Temporal repair stages,
shadow/canary and recovery/release gates remain open. No proposed code or verifier
suite was executed, and no quarantine or capital authority was changed.

Rollback removes this recovery entry point without changing candidate, health,
graph or job-request schemas. Preserve unresolved jobs and receipts; absence of
the recovery feature is not evidence that their containers are absent.

The subsequent local-root ownership increment now serializes cooperating staging,
reconstruction and cleanup through an OS-held lock, including across processes.
See `REPAIR_ROOT_OWNERSHIP_HANDOFF.md`; unknown absence, transactional cleanup and
distributed ownership remain open.

The subsequent removal journal increment records exact owner/container/lease-bound
intent and acknowledgement. It recovers interrupted lease release when the intact
job and current receipt remain, without further daemon access. Missing acknowledgement,
missing lease and partially deleted job recovery remain open. See
`REPAIR_REMOVAL_JOURNAL_HANDOFF.md`.

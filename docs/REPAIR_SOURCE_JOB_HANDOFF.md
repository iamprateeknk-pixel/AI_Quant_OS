# Committed repair sources and disposable verification inputs

## Delivered

`RepairWorkspace.snapshot(candidate_id)` first verifies the actual retained
candidate/incident/manifest/branch/worktree. It then reads **the complete declared
package from its immutable candidate Git commit**, including unchanged dependencies.
It never imports, compiles or executes those files. Package paths must be ordinary
100644 Python blobs or the declared `strategy_manifests.json`; unknown package data,
aliases, nested paths and irregular files are rejected. Working-tree contents are
not used as source. Admission is rechecked after the committed reads.

The immutable snapshot holds exact source bytes, candidate/baseline commits,
proposed manifest and archived incident. A SHA-256 identity binds all metadata and
the complete sorted path/byte-count/source-hash inventory. Decoded metadata views
are copies, so callers cannot mutate the retained source identity through a dict.
Bounds are 256 files, 1 MiB per file, 16 MiB total source, 64 KiB listing and bounded
canonical metadata.

`prepared_repair_job` verifies the exact bounded owner-provided suite bytes against
its SHA-256 pin before writing anything. It creates a fresh owned temporary job
with only `verifier.py`, a schema 1.0 sandbox request and the exact package snapshot.
The request retains candidate/source/suite/version bindings and no promotion
authority. Job verification rejects root identity changes, reparse points,
undeclared directories/files, missing files, hard links, changed bytes and file
identity/size changes. Windows admission uses path-based file identity instead of
the incomplete inode/link fields returned by directory-entry metadata.

This prepares inputs **for the future execution controller**. It is not independent
behavioral verification, a running sandbox, a certificate or a recovery decision.
Neither the suite nor candidate code is executed on the host. The proposed version
is not activated and quarantine/safety state do not change.

## Inspect a candidate source inventory

```powershell
.venv/Scripts/ai-quant-repair.exe --repository D:/AI_Quant_OS --health-store D:/AI_Quant_OS/data-local/paper.health.sqlite3 snapshot <actual-candidate-id>
```

Use the actual journal/root configuration from the candidate handoff. Output is
`SNAPSHOT_ONLY`, source hashes and proposed version, with `promotion_allowed=false`.
It omits source bodies and incident evidence. No exported input folder is retained
by this command.

The Python job context manager is an infrastructure interface for a trusted owning
controller, not an agent tool or client-supplied verdict API. The owner must select
the reviewed suite and trusted root. Temporary inputs are removed on ordinary
completion or consumer error; cleanup only addresses the fresh generated job.
No target-produced output is ingested. The future execution owner must stop/drain
its owned container before leaving the context; this helper does not manage one.

## Verification and limits

Eleven new cases use real temporary Git/SQLite/filesystem and CLI behavior to check
complete committed source retention, proposed-version/incident pins, metadata
immutability, dependency-change identity, source-path/type admission, unknown data
and dirty worktree refusal, exact staged source/request/suite bytes, mismatched
suite refusal before writes, file additions/mutations, hard-link refusal with
outside-link preservation, cleanup after consumer failure and source-free CLI
inventory output. The test risk-file fixture now has explicit LF bytes on both
platforms; production risk code is untouched.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_repair_snapshots.py tests/test_repair_jobs.py tests/test_repair_cli.py tests/test_repair_workspace.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m ruff format --check src tests scripts
```

Current full verification is recorded in `BUILD_STATUS.md`. These checks do not
prove live container isolation, non-root access across every host permission
configuration, hostile-host race resistance, worker correctness or runtime version
compatibility. Job roots must remain owned by the trusted parent and inaccessible
to candidate writes. The future controller must enforce the read-only mount and
resource/network/credential boundary before source execution. Existing local
Docker startup remains unavailable; rejected socket cleanup was not retried.

The original schema 1.0 job includes the archived **health incident**, not the referenced full
LangGraph context checkpoint and datasets needed to replay an actual failed
invocation. Those verified context/source closures must be added before claiming
incident replay. Synthetic tests cannot substitute for that missing evidence.

The subsequent archived-input increment adds an optional schema 1.1 job with the
verified full graph record and captured inline context. See `REPAIR_REPLAY_HANDOFF.md`
for its binding, historical expiry and remaining execution/source coverage limits.

No existing health/candidate schema is migrated. Snapshot identity, CLI summary
and job request use schema 1.0. There is no persistent job journal to migrate and
no target artifact promotion. Rollback removes this source/job preparation layer;
the original candidate commits, incident records and quarantine pins remain.

Next requirements: archived incident-context closure, owned reviewed verifier
program/toolchain, execution controller with bounded output/wall time and owned
cleanup, direct kernel enforcement evidence, actual source/version-bound behavioral
checks, durable verification evidence, Temporal repair stages, shadow/canary,
incident closure and gated recovery/rollback. A16 remains PLANNED and the full build
is not complete.

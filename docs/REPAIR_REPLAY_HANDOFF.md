# Archived repair invocation inputs

This increment prepares verified archival inputs for later isolated verification.
It does not execute a candidate, reproduce a failure, certify behavior or release
quarantine. The existing candidate, health and graph schemas remain unchanged.

## Implemented boundary

`GraphLedger.archive(path)` opens an existing SQLite database in `mode=ro`, without
initialization or migration. Missing files are rejected and never created. Normal
graph reads verify the audit chain, record digest, canonical record, checkpoint
source references and receipt/context/manifest binding.

`load_incident_replay(snapshot, path)` requires the exact canonical health receipt,
agent/version/manifest, failure code and run identity to match the archived graph
receipt. The immutable result binds the complete record hash to the complete
candidate source snapshot hash. Captured inline inputs and their original expiry
are preserved. Returned record views cannot mutate retained bytes.

```powershell
ai-quant-repair --repository D:/AI_Quant_OS --health-store <health.sqlite3> replay <candidate-id> --graph-store <graph.sqlite3>
```

The command emits summary hashes and original run/version identity, with state
`ARCHIVED_INPUTS_ONLY` and promotion disabled. It does not expose captured payloads.

The trusted-parent `prepared_repair_job(..., replay=replay)` rejects another
snapshot's replay before creating directories. Its schema 1.1 request binds
`replay_sha256`, `record_sha256` and replay kind; `incident-record.json` retains
the exact canonical graph record. Existing jobs without replay retain schema 1.0.
Exact file admission and temporary cleanup also cover the added record.

## Evidence and limits

Six added tests use real Git worktrees, audited SQLite and an actual LangGraph
failure. They check original input/expiry preservation, read-only archive bytes,
missing/empty archive refusal, changed/incomplete health receipts, rehashed graph
corruption, foreign snapshot refusal before writes, exact job bytes and command
metadata without payload exposure. Candidate and verifier program bytes are
stored only; neither is imported or executed. Latest full results are in
`BUILD_STATUS.md`. Windows/Linux CI includes these tests; hosted CI is unverified.

The graph checkpoint captures inline inputs and manifest metadata. Dynamic tool
responses and external dataset bytes are not a complete execution transcript.
The deployed original source commit is not pinned in this older graph schema;
the candidate's Git baseline alone cannot prove that it ran the original failure.
The failure test is a controlled infrastructure fixture, not evidence of an
actual repaired production defect.

Next work requires deployed source/transcript closure, a reviewed pinned worker,
historical logical-time execution with explicit proposed-version rebinding,
baseline failure reproduction and independent candidate regression checks.
An execution controller must enforce confinement, wall/output bounds and owned
child termination before temporary cleanup. Live Docker execution remains
unavailable; automatic approval review rejected prior socket cleanup. No equivalent
cleanup workaround was attempted. Certificates, Temporal repair stages,
shadow/canary, incident closure and rollback/recovery remain pending.

Rollback removes this preparation layer; there is no persistent replay journal
or database migration, and existing quarantine and candidate records remain.

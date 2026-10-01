# Isolated repair candidates

## Delivered boundary

`ai-quant-repair` creates an actual local Git commit, `codex/repair-<id>` branch
and separate worktree from a clean committed baseline. The candidate keeps the
archived health incident, exact old manifest, proposed new manifest, source byte
hashes, proposed diagnosis and regression plan in `repair-candidate.json`.
The original commit is its sole parent and retained rollback target.

This is the **candidate construction stage** of P10. Inspection verifies retained
provenance and workspace integrity. It does not independently verify behavioral
correctness, certify the proposed diagnosis or execute replacement Python.
Every receipt remains `AWAITING_INDEPENDENT_VERIFICATION` with
`promotion_allowed=false`. Independent verification, shadow, canary and release
are all pending. A proposed manifest version does not prove that the replacement
implementation emits that version; that compatibility check belongs to the
pending verifier. A16 remains PLANNED.

Only declared A02/A07 0.2.0 implementation sources can be replaced. Capital
controls, broker/idempotency/reconciliation code, shared contracts, sealed strategy
implementations, tests, deployment configuration and permissions are outside this
boundary. Neither the old registry version nor its quarantine changes; the new
version is not registered or activated. No repair is merged or deployed.

## Commands

Install the package's existing dependencies and editable console entry. Use an
existing health SQLite journal whose archived agent failure is available in the
current manifest catalog. The CLI restores quarantine pins from verified journal
records before candidate admission. It never fabricates an incident source.

```powershell
.venv/Scripts/ai-quant-repair.exe --repository D:/AI_Quant_OS --health-store D:/AI_Quant_OS/data-local/paper.health.sqlite3 prepare D:/AI_Quant_OS/data-local/repair-plan.json
.venv/Scripts/ai-quant-repair.exe --repository D:/AI_Quant_OS --health-store D:/AI_Quant_OS/data-local/paper.health.sqlite3 inspect <candidate-id>
```

The journal filename above is an example: use the health sibling of the actual
configured paper database. Default candidate storage is
`<repository>/data-local/repairs`; `--root` can select an external directory.
Tracked source directories and repository ancestors are rejected as storage roots.
Use the same root on restart. Output contains receipt metadata, not replacement
source or incident evidence. Errors are sanitized.

A schema 1.0 plan contains exactly these fields:

```json
{
  "schema_version": "1.0",
  "incident_id": "<actual 64-character archived incident hash>",
  "new_version": "0.2.1",
  "replacements": {
    "src/ai_quant_os/regime_agent.py": "<complete proposed UTF-8 source>"
  },
  "diagnosis": "Evidence-based proposed root cause, including unresolved uncertainty",
  "regression_plan": ["Replay the exact failed incident", "Check envelope expiry and authority"]
}
```

This template is intentionally not an executable fixture. Supply the actual incident
and complete proposed source. Client promotion flags, verdicts and unrecognized
schema fields are rejected. Versions must strictly increase and cannot already be
registered. Each replacement must differ byte-for-byte from its committed source.
Python files must be ordinary non-executable Git blobs. Paths are exact allowlist
matches; traversal and case aliases are rejected.

## Persistence and failure runbooks

- **Interrupted construction:** replay the same plan against the same committed
  baseline. The deterministic candidate identity and commit permit adopting the
  exact existing branch, receipt and clean workspace. Receipt publication uses a
  flushed temporary file plus exclusive hard link; partial JSON is not published.
- **Changed branch, workspace or receipt:** inspection refuses the candidate.
  Preserve it for investigation. Do not erase the health incident or use candidate
  inspection to clear quarantine. A deliberate revised proposal requires its own
  plan and identity.
- **Failed repair or unsuitable proposal:** retain the dossier as unapproved
  evidence. Production still uses its original source and no release happened.
  Rollback automation for an activated version is not implemented; the preserved
  baseline is evidence for the future gated mechanism.
- **Changed main baseline:** a new commit produces a different candidate identity.
  This facility records repository provenance, not a claim that the repository's
  HEAD equals a deployed production image.

Git objects and refs plus owned immutable receipt files are the local persistence
format; the health schema is unchanged. There is no migration of existing journal
rows. Receipt and dossier schema version is 1.0. Each replacement is at most 1 MiB,
plans 3 MiB, serialized dossiers 64 KiB, and receipt reads 8 KiB. Git operations
have a 30-second timeout, disable hooks via an owned empty hook directory and do
not inherit operator/provider credential environment variables. Existing trusted
repository checkout filters remain Git configuration; this is not an OS sandbox.

Single-owner local operation is supported. Interrupted Git worktree registration,
concurrent writers, disk exhaustion and externally moved repositories can require
operator diagnosis; there are no automatic destructive cleanup operations.
Root directory ownership, Git objects and receipt files must remain trusted.
Local hashes do not provide external tamper-proof signing.

## Verification

The added tests exercise actual temporary Git repositories, commits, branches,
worktrees and audited SQLite incidents. Cases include unchanged original HEAD and
source, retained baseline, restart adoption, manifest/version/quarantine admission,
capital-code/traversal denial, dirty/no-op refusal, receipt/worktree/ref tampering,
interrupted publication, rehashed executable-mode tampering, escaped JSON overflow,
strict CLI fields, missing journal refusal and non-execution of candidate source.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_repair_workspace.py tests/test_repair_cli.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m ruff format --check src tests scripts
```

CI includes this local candidate boundary on Windows and Linux. Hosted execution
is unverified. Full current counts and compatibility evidence are recorded in
`BUILD_STATUS.md`. No paid provider, live broker, independent certificate or
candidate activation is claimed.

Changed files: `repair_workspace.py`, `repair_cli.py`, their two test modules,
console registration in `pyproject.toml`, CI, this handoff, ADR 0035, README and
build status. Next dependencies are independent source/version-bound behavioral
verification with proper execution isolation, durable Temporal repair stages,
shadow/canary evidence, incident closure and gated recovery/rollback.

# Full serial strategy prefix run

## Prepare and run

The operator entry point is `python -m ai_quant_os.strategy_prefix_run_cli`.
It reads the existing immutable validation-package archive and the existing pinned
worker image. `prepare` inspects/verifies the image handoff and writes a canonical
`strategy-run.json` plus audited `strategy-run.sqlite3` under a new empty ordinary
run root. Retain the `run_sha256` printed at preparation; recovery never derives a
trust pin from a newly read mutable manifest.

For preparation, pass common `--validation-archive`, `--package-ref` and `--root`,
then `prepare` with the existing image inputs:
`--image-archive`, `--base-metadata`, `--base-manifest-digest`, `--worker-sha256`,
`--archive-sha256`, `--manifest-digest` and `--config-digest`. `--quantity` defaults
to 1 and is limited to 1–500,000; `--initial-balance` defaults to 100000 and must be
positive and at most 1 trillion. Preserve the run hash separately.

Invoke `run` with the same common arguments and the independent `--run-sha256`. The
runner validates the original package and expected references, then processes every
original non-warmup bar serially. For each index it saves a unique RESERVED job
identity before staging, binds the complete stage, prepares/rechecks the pinned image
and owned create recipe, creates and starts at most once, checks the canonical target
and successful terminal metadata, requires acknowledged exact removal, then archives
the completion. The two warmup bars remain zero targets. The runner stops on the first
uncertain/failed prefix and never skips ahead. Retained completed job directories are
released after each full receipt and recovery archive have been durably audited, so
the old 32-directory staging limit does not limit an uninterrupted full run.

After all decisions are complete, the runner performs BASE and DECLARED_STRESS native
replays against the same numeric tape, quantity and balance. It publishes the single
`strategy-run-replay.json` report and stores its digest in the run audit. The CLI
prints the report reference and digest; retain them independently.

## Recovery

The audit keeps one unique reservation per source index and never silently retries an
uncertain phase. An interrupted successful release is restartable: because its exact
removed receipt/archive was committed before deleting inputs, a later `run` rechecks
that archive and removal history, finishes release and continues at the next index
without starting the completed worker again.

For a fully archived but not yet acknowledged per-prefix recovery bundle, use
`recover-prefix` with `--run-sha256` and that original independently retained
`--release-sha256`; this records only the reserved index and cannot change its source,
image or invocation binding. Uncertain staging/create/start or rejected output still
requires explicit per-prefix investigation and reconciliation. Do not delete, edit,
replace or reuse such a job/archive. If a valid replay report was published but the
run completion record was interrupted, use `recover-report` with the original
`--run-sha256` and independently retained `--report-sha256`. A malformed/missing
report or missing/corrupt journal is never recreated or inferred.

Run SQLite journal size is bounded to 128 MiB and retained recovery data to 8 GiB;
completed receipts/archives/owners are intentionally retained. Future retention and
compaction policy is still required. The run root must have the exact original native
parent identity chain and cooperative locks. Do not run other job operations against
that root concurrently.

## Results and limits

The manifest/report schemas are `docs/schemas/strategy-prefix-run-manifest.schema.json`
and `docs/schemas/strategy-prefix-run-report.schema.json`. All execution/promotion
authority remains false. The report is native numeric replay of operator receipt
metadata, not source-behavior attestation, statistical acceptance or approval.

Full-run tests cover complete package decision inventory through actual native replay,
BASE/stress identity, explicit CLI, duplicate-run refusal, interrupted phase handling,
per-prefix source-at-most-once, local SQLite corruption, independently pinned prefix
recovery, report publication recovery and refusal of untrusted output. Scripted daemon
observations are tests, not real Docker/worker/kernel evidence. The locally available
Docker Linux endpoint was unavailable at ADR 0078 verification. ADR 0079 now records
an actual local synthetic twelve-bar run with ten worker decisions, image/container
admission, exact target comparison, removal, native BASE/stress replay and a fresh
process retrieving the identical report. See `STRATEGY_ENGINE_HANDOFF.md` for the
real-engine test and `docs/BUILD_STATUS.md` for remaining release gates. Independent
kernel/source attestation and statistical acceptance remain open.

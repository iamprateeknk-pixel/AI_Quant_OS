# Checked validation interruption reports

An original validation job can stop before there is a proposed-test result or a
statistical scorecard. Its interruption report retains the original package,
candidate, job hash, stage record and exact audited checkpoint. The boundary names
the declared stage: proposed tests, holdout, development or result publication.
It does not infer which container operation failed or whether a worker started.

## Operator commands

Use the existing locked `python -m ai_quant_os.candidate_validation_job_cli`
command with global `--validation-archive`, `--package-ref` and `--root` arguments.

| Command | Required additional arguments | Result |
| --- | --- | --- |
| `interruption` | `--job-sha256`, `--output` | Exclusive canonical report file, report reference and independent byte hash |
| `verify-interruption` | `--job-sha256`, `--input`, `--interruption-sha256` | Verifies supplied original bytes and their historical checkpoint in the original job audit |

The output directory must already exist. Export cannot overwrite an earlier file,
source, journal or manifest. Verification rejects changed bytes, noncanonical JSON,
foreign job/package bindings, changed stage/head/count, unknown fields, altered
original audit history and acceptance/cleanup claims. Both commands work without
the optional native trading engine or a worker daemon. They do not invoke workers,
cleanup, replay, publication or recovery.

Only RESERVED, PREPARED and UNCERTAIN stages can be captured. An unstarted job,
completed stage or safe cooperative pause cannot be labeled an interruption.
Historical verification remains possible after independently pinned recovery,
because the retained checkpoint is checked against the original append-only audit.
It does not assert that the job is currently interrupted. The job's existing
`status` command remains the current-state reader.

## Meaning and scope

For source-test worker detail, use the independently pinned reader in
`CANDIDATE_TEST_CHECKPOINT_HANDOFF.md`. It distinguishes retained create/start/
terminal/removal/result checkpoints without changing this stage-level contract.

Outcome is VALIDATION_INTERRUPTED. Recovery is required; cleanup, isolation and
promotion verification are false. There are no trading statistics, candidate
rejection claims, passed gates, exception messages, source bytes or filesystem
paths in the report. Source-test failures with confirmed results retain their
separate prerequisite-failure contract. Numeric scorecards retain their original
contract. Interruption reports are explicit operator files; A10's checked scorecard
archive and graph role remain unchanged.

This closes local historical stage-interruption observation. Granular worker
create/start/exit/removal classifications, independent daemon/source attestation,
physical/statistical isolation, independent acceptance, full missions and production
release gates remain open. See VALIDATION_JOB_HANDOFF.md for actual reconciliation.
Verification evidence is recorded in `docs/evaluations/validation-interruption-0088.json`.

## Verification and retained uncertain jobs

The 16 boundary/tamper/command checks passed; the fresh staged checkout passed 40
checks with one opt-in worker case skipped. Actual Linux/Temporal lost-acknowledgement
recovery, historical interruption verification and final publication passed.
The full run recorded 2,016 passed, 15 skipped and three failures. Its scorecard
timeout passed twice on recheck. Both Docker create cases passed in a new root
after restoring the stopped engine's runtime socket directory. Original failures
remain in the evidence; no full-green-run claim is made.

Do not retry or release these original uncertain inputs automatically:

| Original evidence root under `data-local/full-tests-0088` | Owner | Container name |
| --- | --- | --- |
| `test_real_engine_full_prefix_r0/run` | `807bfc0b5b2241ddb18a18795eb32532` | `ai-quant-strategy-807bfc0b5b2241ddb18a18795eb32532` |
| `test_actual_privilege_and_reso0` | `46e8e451679e4da5b7021f98b7860901` | `ai-quant-repair-46e8e451679e4da5b7021f98b7860901` |

The first root retains job `6f6396ceb9544adc9edd1ffd6bfd2596` and a
CREATE_REQUEST_MAY_HAVE_BEEN_SENT marker. The second retains its original
create-intent and source hashes without a create acknowledgement. These are
separate from the earlier retained 0084 uncertain job. Fresh rechecks used new
names and roots, and do not reconcile or grant cleanup authority over any of them.

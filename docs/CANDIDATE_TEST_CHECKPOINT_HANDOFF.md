# Original source-test worker checkpoints

Use this reader to inspect retained custody when a candidate source-test job stops
before a validation scorecard exists. It uses the original source-only job and its
independent manifest pin. It does not run proposed code or contact Docker.

## Commands

Use the locked Python runtime with
`python -m ai_quant_os.candidate_test_cli --root <original-source-test-root>`.
For a validation job, the source-test root is its `candidate-tests` directory and
the pin is `candidate_test_manifest_sha256` in the original validation-job manifest.

| Command | Additional arguments | Result |
| --- | --- | --- |
| `checkpoint` | `--manifest-sha256`, `--output` | Exclusive canonical report and independent report hash |
| `verify-checkpoint` | `--manifest-sha256`, `--input`, `--checkpoint-sha256` | Checks original report bytes and retained historical worker custody |

The output directory must exist and must be outside the source-test custody root.
Existing files cannot be overwritten. The reader checks original native filesystem
identities, source/SDK/request hashes, ownership, admission, coherent prerequisites,
terminal exit and removal/result bindings before publishing any checkpoint.
Changed bytes, foreign owners, weakened confinement, missing prerequisites,
duplicate JSON keys and altered result bindings withhold output.

## Checkpoint meaning

| Checkpoint | Last retained evidence |
| --- | --- |
| `NO_CREATE_INTENT_RETAINED` | No create intent in this snapshot; not proof that no create occurred |
| `CREATE_OUTCOME_UNCONFIRMED` | Original create intent; no retained create acknowledgement |
| `CREATE_ACK_RETAINED` | Original immutable container ID acknowledged |
| `CREATED_ADMISSION_RETAINED` | Original owned container inspection passed confinement checks |
| `START_OUTCOME_UNCONFIRMED` | Original start intent; no retained terminal observation |
| `TERMINAL_EXIT_RETAINED` | Original owned successful terminal exit observation |
| `WORKER_OUTPUT_RETAINED` | Source-bound original worker output; removal may still be unconfirmed |
| `REMOVAL_OUTCOME_UNCONFIRMED` | Original removal intent; no retained removal acknowledgement |
| `REMOVAL_ACK_RETAINED` | Original removal acknowledgement; inspect the file inventory for start/output evidence |
| `RESULT_RETAINED` | Original retained result matches worker output and acknowledged removal |

Cleanup can occur before admission/start, or after an unacknowledged start.
A removal acknowledgement does not invent a start, exit or source-test result.
The report includes exact metadata hashes and an optional original test status,
but no test counts, trading statistics, source bytes, paths or exception messages.

## Historical verification and limits

Verification permits additional valid original records after capture. Every file
used by the saved report must still exist with exactly its saved hash. It verifies
retained positive evidence; it does not establish the historical absence of other
files, wall-clock timing or the current container state. Original custody remains
under the same local trusted-filesystem assumptions as the execution controller.

The report is not a cleanup certificate or a worker-failure attestation. All
`worker_failure_confirmed`, `cleanup_verified`, `isolation_verified` and
`promotion_allowed` fields are false. Use existing explicit original-result
reconciliation and cleanup commands for authorized recovery. The reader never
retries, releases files or changes current job state. Existing 0084/0088 uncertain
jobs remain retained without automatic reconciliation.

Strategy-prefix checkpoint reporting, independent daemon/source attestation,
physical/statistical isolation, independent acceptance, full missions and production
gates remain open. See `VALIDATION_INTERRUPTION_HANDOFF.md` for stage-level reports.

## Verification

Focused verification passed 45 checks, including real admitted Linux completion
and lost-start-acknowledgement cases. The fresh staged checkout passed 44 checks
with those two opt-in worker cases skipped. All 200 surrounding candidate,
validation archive, A10, interruption and Temporal checks passed with the relevant
four Linux/Temporal gates enabled. Original execution and wire models remain
unchanged. This is a scoped regression, not a new full-repository green run.
Both original real-worker checkpoint records were revalidated without daemon calls.
Exact counts, timings, custody records, preservation checks and remaining gates
are retained in `docs/evaluations/candidate-test-checkpoint-0089.json`.

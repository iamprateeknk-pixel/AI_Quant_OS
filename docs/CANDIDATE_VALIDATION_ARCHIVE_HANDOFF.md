# Checked candidate validation archive

The original validation package remains unchanged. Compilation and proposed-test
failures produce a separate `CandidatePrerequisiteFailure` retaining the exact
source-bound test observation and its hash. The fourteen downstream gates are
explicitly unexecuted for that attempt. No P&L, fills, closed trades or Monte Carlo
results are fabricated. Passed observations and foreign package, candidate or test
request references cannot be recorded as prerequisite failures.

The existing byte-pinned validation command now supports:

```text
--validation-archive <original package archive>
--package-ref <original package reference>
--test-result <canonical test-result path> <independent SHA-256>
record-failure --output <new canonical failure JSON>
```

`verify-failure --failure <path> <independent SHA-256>` checks the retained artifact
against the original package and test bytes. Failure commands reject downstream
reports and policy arguments. New JSON outputs use exclusive creation and fsync;
existing files are never overwritten. These commands inspect metadata and do not
compile, import or execute proposed code.

For durable checked publication, use the same global input options with
`--record-archive <candidate-results.sqlite3>` and `archive-failure`. Use
`archive-evaluation` with the original holdout and every ordered development report
to publish a scorecard. These archive commands do not also publish JSON files.
Scorecards are recomputed through the original numeric validator before publication;
the public store does not accept arbitrary prebuilt scorecards. Duplicate checked
publication returns the retained artifact without adding an audit event.

SQLite publishes each artifact and its byte-bound audit event in one transaction.
Every read and reopen verifies the complete bounded archive, canonical identities,
artifact hashes and audit chain. Changed bodies, missing audit metadata and capacity
violations refuse access. Capacity is at most 32 artifacts, 4 MiB per artifact and
32 MiB overall. Keep the original package, independent pins and source reports for
fresh reproduction; this archive retains checked results and their references.

The audit assumes trusted local storage and publisher code. It is not independent
source attestation, physical holdout isolation, a certificate, agent acceptance or
promotion permission. All authority flags remain false. A10 mission integration,
agent health handling, complete larger-trial validation and the remaining full
execution-pack requirements are still open.

## Verification

The full regression with all six existing local web/Linux gates passed 1,963 tests,
with 15 optional integrations skipped. A fresh staged checkout passed all 14
focused failure/archive/scorecard checks. The source, tests and exported schema
match the staged index. The full run's real-worker reports reproduced their
original scorecard through checked publication and a fresh archive reader. Prior
wire schemas and unchanged worker artifacts were preserved; staged secret scanning
found no leaks. See `docs/evaluations/candidate-validation-archive-0085.json`.

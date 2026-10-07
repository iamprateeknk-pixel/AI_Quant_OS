# 0085: Retain checked candidate observations separately from original packages

Date: 2026-10-07

## Context

Original packages capture immutable candidate/source/dataset inputs with NOT_RUN
statuses. A failed source-only prerequisite must be retained without fabricating
the downstream trading observations required by a statistical scorecard. Completed
scorecards also need durable checked publication for later validation-agent reads.

## Decision

Add a distinct immutable prerequisite-failure contract carrying the original test
result, its canonical hash, original package/candidate references and unexecuted
downstream gate inventory. Reconstruct the original test request before accepting
the record. Retain all false authority claims and preserve original package bytes.

Publish failures and scorecards through a bounded SQLite store. Public publishers
accept original observations; scorecards are recomputed before insertion. There is
no public publication operation accepting arbitrary prebuilt scorecards. Artifact
and hash-bound audit publication share one transaction. Verify the complete archive
before reading, deduplicating or writing. Refuse missing or changed metadata rather
than repairing it. Duplicate publication retains one artifact and one audit event.

Extend the existing byte-pinned command with separate JSON failure record/verify
operations and archive failure/evaluation operations. Archive commands require an
explicit result archive and publish only there. Failed prerequisites reject reports
and policy arguments. These operations perform no proposed-source execution or
Docker calls.

## Consequences

The local archive retains results and original lineage references; it requires the
original package, independent hashes and reports for fresh reproduction. Local
audit integrity is not independent attestation or protected external storage. A10
mission integration, bounded health/error handling, adversarial/source acceptance,
physical/statistical isolation, larger declared computation plans and all full
production release requirements remain open. This change grants no capital or
promotion authority.

## Verification

Full regression: 1,963 passed, 15 optional skips, all six existing local web/Linux
gates enabled. Fresh staged checkout: 14 focused checks passed. Checked archival
reproduced the full run's real-worker scorecard. All 24 prior wire schemas and 301
unchanged production/infrastructure/schema artifacts were preserved. See the
0085 evaluation artifact for timings and remaining requirements.

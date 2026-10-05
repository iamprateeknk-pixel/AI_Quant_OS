# ADR 0080: Linux dependency runtime probe

Date: 2026-10-05
Status: Accepted for local dependency verification

## Decision

Use the original pinned dependency-only image to verify the Linux test toolchain
before supplying proposed source or tests. A trusted standalone probe accepts only
the exact bounded sorted inventory of 51 distribution names/versions. It rejects
candidate/source/SDK/test/data fields and performs its runtime guard before any
third-party import or scratch write.

The opt-in integration reuses the established fixed repair create recipe, common
confinement predicate and bounded local Docker transport. It records durable create
and start intents, checks original probe/request bytes before start, validates the
successful terminal response, and removes only the re-inspected exact owned
container. This is a development probe, without general candidate dispatch or
automatic orphan recovery.

## Verification

Initial red checks showed the missing probe. A real run then refused at environment
admission because CPython adds its UTF-8 locale field. The same exact optional
locale behavior is already handled by the verified strategy worker. A focused red
case reproduced that mismatch before the correction. Other locale, HOME, LANG and
PYTHONPATH changes remain denied.

The final focused run passed 12 tests in 3.24 seconds, including real Linux
execution and cleanup. All 51 distribution versions matched the lock; core package
imports and two trusted native Pydantic/SQLite tests passed. Actual read-only write
attempts failed with `EROFS`, and an external-route attempt was denied. Lint and
format passed. A clean staged checkout passed all 12 checks in 4.45 seconds, including its real Linux probe. All 17 existing schemas and reused worker/custody/execution/replay/release sources match HEAD. The frozen full repository regression with both real-engine gates passed 1,869 tests, with 15 optional integrations skipped, in 907.74 seconds. See `docs/evaluations/dependency-runtime-0080.json`.

## Limits and rollback

The original image, worker, package/source bindings, domain/application APIs and
archive schemas are unchanged. Observed dependency behavior is not independent
isolation certification or proposed-test acceptance. Complete candidate compilation
and proposed tests must run in a separate no-holdout request/job with exact source
and toolchain binding, checked execution/output, durable ownership and recovery.
Production repair coordination, shadow/canary, promotion and live gates remain open.

Disable the opt-in gate or revert this probe/test to stop future checks. Preserve
partial-run intents and removal records; shared image cleanup is not part of a
source rollback. The successful container was removed, while its mounted probe,
metadata-only request and observations remain archived locally.

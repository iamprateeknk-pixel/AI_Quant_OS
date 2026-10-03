# ADR 0058: Audited synthetic event evidence and partial A06

Date: 2026-10-03

## Decision

Implement A06 0.2.0 as a partial shadow mission over an operator supplied
synthetic calendar/classification feed and an atomic local paper unit ledger.
Separate deterministic proximity policy, strict evidence contracts, immutable
audited storage, HTTP publication and the graph adapter. Retain unavailable feed
states and source expiry. No event output grants order or risk-policy authority.

Normalize timestamps to UTC before comparisons. Bound event count, identifiers,
coverage, expiry, numeric representations, archive size and context bytes.
Represent instrument associations as indices into one full measurement array
so maximum supported inputs fit the existing output budget without truncation.

Use a separate local SQLite archive with atomic feed/journal publication and
verified historical reads. Refuse missing integrity metadata rather than
re-baselining it. Offload A06 storage reads from the asynchronous context route;
unavailable storage returns 503. A06 does not depend on world price arithmetic.

Tighten paper HTTP market admission to configured instruments and finite bounded
prices after reproducing an extreme-price overflow. Validate complete OHLC batches
before mutation. Keep sealed trading and risk implementations unchanged.

## Evidence and limits

Retain a seven-case synthetic actual-graph baseline, named source/evaluator/recipe
hashes and two schemas. Actual API tests cover restart, exposure, missing/stale/
future feeds, authorization, storage locks, source tampering, maximum event
associations and independent stale/spread order rejections. Complete verification
is recorded in the build status when the regression run ends.

The archive is finite, locally anchored and has no rotation or independent
external integrity anchor. Real providers/classifiers, calibration, distributed
publication, production scheduling, A06 repair/evolution integration, independent
review and all promotion/live-release gates remain open.

## Rollback

Preserve all state, event archive and receipts with the local app stopped before
reverting. No shared database migration occurs. Restore a compatible reader for
archived A06 0.2.0 receipts; do not promote the planned original manifest.

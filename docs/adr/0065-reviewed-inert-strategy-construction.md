# 0065: Reviewed inert strategy construction

Date: 2026-10-04

## Decision

Connect A09 0.2.0 to a fixed completed-bar direction template and explicit
operator authorization for one retained A08 hypothesis. Keep review authorization
separate from alpha acceptance, candidate validation, execution and promotion.
Bind the grant to the original checked graph and selected hypothesis, require
bounded expiry, preserve idempotent review IDs and append immutable withdrawals.

Publish candidate source, configuration/genome, proposed tests and assumptions as
inert artifacts through the task-bound CANDIDATE_CREATE tool. Verify complete
bounded review/withdrawal/candidate collections and exact SHA-bound audit events
inside serialized SQLite transactions. Refuse damaged or missing metadata and
never recreate it for an existing database. Current withdrawal and expiry gate
publication, including idempotent returns. Keep the built-in registry unchanged.

Revalidate typed fields before source interpolation. A09 output must match the
retained context recipe exactly. Check current task/scope/permission/context and
review admission around owned writes. Post-publication expiry/cancellation can
retain archival inert bytes but must withhold a successful current output.

## Evidence

Tests first reproduced missing review/build routes and missing store operations.
A model copy initially allowed arbitrary instrument text into the renderer;
round-trip validation now rejects it. An enabled-health review initially called
a nonexistent store method; it now uses the existing checked health admission.
Review status initially ignored current A09 capability; the actual permission
check now prevents a false current authorization claim.
The final owned review read initially could outlast context validity; a controlled
clock regression reproduced an expired READY reply. Final context admission now
runs after that read, refusing the reply while retaining only the archival checkpoint.

Two actual API development replays retain exact reviews, candidate artifacts and
graph receipts across fresh app restart with unchanged controls. A wrong declared
rule fails matching. Source/evaluator/fixture pins and three typed schemas are
retained. Full regression evidence is recorded in BUILD_STATUS.

## Limits and rollback

The parent does not import, compile, run or register proposed code or test source.
No operating-system sandbox, statistical quality or calibrated alpha is claimed.
The SDK profile observes two local source hashes, not a complete independently
anchored dependency closure. The installed runner does not support these proposed
sources. Broader generation, isolated validation, holdout separation, independent
review and promotion remain open. Preserve reviews, withdrawals, candidates and
all source/context/graph/quarantine state on rollback. See STRATEGY_BUILDER_HANDOFF.

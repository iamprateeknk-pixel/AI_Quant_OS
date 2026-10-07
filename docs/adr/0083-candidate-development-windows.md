# ADR 0083: Original-source development windows

Date: 2026-10-07
Status: Accepted for local development execution

## Decision

Introduce an explicit dataset view over the original validation package's training
receipt. Bind each window to original candidate, package, data, declared plan and
passed proposed-test observation. Preserve two closed history bars before each
validation interval. Retain a partial final interval explicitly rather than silently
discarding its remaining data. Reconstruct every view before preparing or executing
an invocation.

Use separate window run manifests and reports. Pin the complete window before
container creation, including same-sized fold distinctions. Reuse the existing
serial custody, start, removal, release and native BASE/stress replay controls.
The original holdout wire models and fixed worker/image/guard/policy remain unchanged.
The operator CLI requires independently pinned retained candidate-test evidence.

The original candidate was designed using training data. These retrospective
development windows therefore explicitly record selection overlap and unassessed
statistical independence. They grant no isolation, execution-verification or
promotion authority.

## Verification

Malformed history and reversed validation intervals first failed refusal tests;
the window validator now refuses both. Changed fold, fingerprint, original data,
test-observation and completeness fields are refused during reconstruction.
Three missing retained wire schemas first failed their publication gate.
The final focused run passed 20 checks in 38.66 seconds, including source-only
candidate compilation/tests, both actual Linux development folds, owned cleanup,
BASE/stress native replay and fresh-process cached retrieval.

The frozen full regression with all six local gates passed 1,949 tests, with 15 optional integrations skipped, in 1042.19 seconds. The clean staged checkout passed all 20 checks in 41.40 seconds, including actual Linux execution. All 19 prior wire schemas match models and HEAD; 277 unchanged production/infrastructure/schema artifacts match HEAD after Git newline normalization. Lint/format (380 files), staged source/test/schema comparison and secret scan passed. See docs/evaluations/candidate-dataset-windows-0083.json. The full P00–P13
build remains incomplete. Independent source/daemon attestation, physical holdout
isolation, statistical acceptance, champion gates and production release remain open.

## Boundaries and rollback

Dataset parsing and lineage stay in the explicit view module; wire validation
stays in its contracts; existing infrastructure controllers accept the declared
input union. The CLI composes retained evidence. No proposed source runs on the
host. The architecture declaration lacks machine-readable layer rules; review is
manual. Rollback removes the window contracts/view and CLI composition and restores
the prior original-package input bindings. Retained original holdout records remain
compatible.

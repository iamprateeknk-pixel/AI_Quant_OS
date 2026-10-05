# ADR 0081: Separate source-only candidate compilation and tests

Date: 2026-10-05
Status: Accepted for local development verification

## Decision

Provide a separate candidate test request, durable job controller and explicit CLI.
Revalidate the original package before copying only candidate/proposed-test/SDK
source bytes. Keep all dataset payloads outside the mounted request. Preserve the
original candidate/package capture contracts and immutable image/strategy worker
pins. Export two new independent request/result schemas.

Use the existing fixed Docker create recipe, confinement predicate and bounded
local transport. The trusted shared guard retains its dependency-probe profile and
adds one fixed candidate-test profile, with exact three-file inventory and pinned
guard bytes. Check runtime controls before compiling/importing proposed sources.
Run six current renderer test cases in the pinned Linux toolchain.

Require an independently retained manifest digest and unchanged native filesystem
bindings for every operation. Persist/fsync intent before create/start, never retry
uncertain execution, admit immutable container ownership, validate terminal state,
retain exact removal acknowledgement, and publish results only after input recheck.
Fresh-process retrieval checks retained evidence without another start. Explicit
cleanup uses original ownership and refuses uncertain absence.

## Verification

Missing producer/worker/controller/CLI/schema behaviors first failed their tests.
Numeric false authority and malformed terminal metadata were reproduced before
their corrections. The actual worker compiled six retained files and ran six
proposed tests successfully. The focused gate also exercised a lost start
acknowledgement after real worker exit, acknowledged owned removal, refusal to
execute again and fresh CLI result retrieval. Lint/format and preservation of the
17 original schemas and existing strategy/custody/execution/replay/release sources
passed. A clean staged checkout passed all 56 focused checks in 22.27 seconds, including the real engine. The frozen full regression with all four local gates passed 1,913 tests, with 15 optional integrations skipped, in 1039.16 seconds. See `docs/evaluations/candidate-tests-0081.json`.

Manual dependency review: new request/result contracts and inert producer depend
on existing checked package contracts; filesystem custody owns persistence; Docker
orchestration owns lifecycle; CLI owns archive lookup/presentation. No domain
module acquires infrastructure imports. The existing architecture file lacks a
machine-readable layer declaration, so automatic boundary checking is unavailable.

## Limits and rollback

The full AI Quant OS build remains incomplete. In-process proposed tests are
unattested observations and can be influenced by source under test. They grant no
runtime registration, execution, isolation certification, statistical, champion,
promotion or production/live authority. Independent source/kernel attestation,
adversarial validation, physical holdout isolation and all remaining P00–P13 gates
remain open. Trusted filesystem/daemon assumptions and retained input cleanup
limits are documented in CANDIDATE_TEST_HANDOFF.md.

Rollback removes the separate modules/harness/schemas/commands and the fixed extra
guard profile; original image, strategy execution and wire contracts are unchanged.

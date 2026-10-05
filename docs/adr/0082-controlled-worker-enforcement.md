# ADR 0082: Controlled adversarial worker enforcement

Date: 2026-10-05
Status: Accepted for local development verification

## Decision

Exercise the actual fixed worker kernel controls with five trusted, bounded,
metadata-selected adversarial scenarios before relying on them in statistical
validation missions. Reuse the immutable dependency image, existing guard,
create recipe, confinement predicate and local bounded Docker transport.
Introduce no production source, image, policy, limit or wire-contract changes.

Check privilege/raw-socket and scratch noexec denial; deliberately exceed memory,
CPU, child-process, file-size and descriptor ceilings only inside exact owned
containers. Persist source/recipe/create/start/terminal/removal evidence and verify
successful expected denials or exact expected owned kernel kills. Do not loosen
limits or reinterpret ordinary probe refusal as enforcement success.

## Verification

Missing probe/admission/host refusal first failed ten checks. An initial real run
refused the proposed 65-second transport before any worker start; those unstarted
owned containers were acknowledged removed. Corrected the harness to the existing
45-second bound. All four initial real scenarios passed. A missing file/descriptor
scenario then failed before implementation. The final focused gate passed all
16 checks in 36.19 seconds, including all five actual Linux scenarios.

The real observations cover `EPERM`, `EACCES`, `EAGAIN`, `EFBIG`, `EMFILE`, OOM
kill metadata and a CPU-limit kill. Lint and formatting passed (375 files).
The original 17 wire schemas and reused strategy/custody/execution/replay/release
bytes match HEAD. Final regression, cold checkout, source preservation and secret
scan evidence is recorded in `docs/evaluations/worker-enforcement-0082.json`. The clean staged checkout passed all 16 checks in 36.78 seconds. The frozen full regression with all five local gates passed 1,929 tests, with 15 optional integrations skipped, in 853.02 seconds. Broad Git-normalized preservation of all existing production, worker/guard/image and schema inputs also passed; only the worker README was intentionally updated.

Manual review: trusted Linux operations live in the infrastructure probe; metadata
validation and non-Linux refusal precede them. The test harness owns opt-in Docker
composition and evidence retention, with exact ownership checks before removal.
No inner/domain module imports the probe. The architecture file has no supported
machine-readable layer declaration; review is manual. The structure map is
updated as local evidence.

## Limits and rollback

The full P00–P13 build remains incomplete. These are observed local kernel controls
on a trusted parent filesystem/daemon, not independent cryptographic attestation.
Process ceiling attribution is deliberately combined. Arbitrary malicious-source
behavior, physical holdout isolation, statistical/champion acceptance, complete
agent missions and production/live release gates remain open.

Rollback removes the isolated probe, opt-in harness and handoff/evidence; original
worker image, guard, execution controller, strategy source and contracts are
unchanged. Original false authority fields remain false.

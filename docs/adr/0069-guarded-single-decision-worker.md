# ADR 0069: Guarded single-decision candidate worker

## Status

Trusted harness and image definition prepared. Actual image/engine execution and
parent ownership/admission lifecycle remain pending and unverified.

## Decision

Use a standalone standard-library harness in a separately pinned Python image.
Stage only one exact prefix request, with the candidate/SDK bytes and two prior
closed bars retained inside that JSON. Candidate materialization and import are
reachable only after actual runtime prerequisite inspection and strict wire checks.
The parent owns original package binding, image/harness pins and full container
admission. A fresh lifetime and empty scratch are required for each decision.

Retain the existing unattested numeric response schema. Worker output cannot claim
external confinement, source/test attestation or promotion. Candidate console noise,
missing/foreign output, timeout/resource termination and uncertain cleanup must
remain failures at the future parent boundary.

## Consequences

Host tests exercise only trusted parsing, metadata guard predicates, response encoding
and the real host-runtime refusal path. Proposed candidate code and tests remain
unexecuted. Matching synthetic runtime metadata is not kernel or behavior evidence.
No image build, Docker create/start or candidate evaluation occurred in this increment.

The observed process/mount/cgroup checks are secondary controls, not proof of complete
namespace/mount isolation or a replacement for trusted external admission. Full image
closure/pins, actual Linux enforcement, owned container execution/recovery, separate
proposed test jobs, independent source attestation, statistical acceptance, A10 and
all production release gates remain open. See infra/strategy-worker/README.md.

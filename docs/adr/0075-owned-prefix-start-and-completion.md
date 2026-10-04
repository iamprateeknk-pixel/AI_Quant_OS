# ADR 0075: One owned attached prefix start and unattested completion

## Status

Implemented execution control and strict local completion evidence. Tests use
simulated daemon observations; actual Docker Linux engine execution is unavailable.

## Decision

Extend the existing owned container lifecycle with one cooperative custody session.
Reinspect original immutable archive/image/engine/source and created metadata, bind
a fixed 45-second attached start request, fsync an exclusive attempt marker before
one RPC and never retry a candidate start. Keep bounded output and an independent
selected successful exit-state check separate from untrusted target validation.

Attempt owned kill/force removal on every attempted start path. A stopped parent
Docker command is not container cleanup. Only exact inspected owner/ID and a removal
acknowledgement permit final target/receipt publication. Changed inputs still allow
removal of the already admitted exact owned ID before rejecting output publication.
Keep all input/sidecar custody after removal; no automatic deletion or repair.

Bind the original create/start/output/exit/removal identities in a strict frozen
model/schema. Retain false execution/isolation/promotion authority. Restore only
with the original package/plan and independently pinned receipt/hash; a removed
owner is restored without any daemon call. Rehashed unrelated start metadata fails
model binding, not merely syntactic schema validation.

## Limits

Created/exit metadata and local audit history are not independent kernel or source
attestation. The worker source and older output schema remain unchanged. The parent
attachment transport deadline has separate bounded cleanup calls; hard termination
while the daemon is unavailable is not certified. Publication can retain partial
files; root writers/host namespace are trusted and power-loss directory-entry
durability is unverified. Actual runtime execution, separate proposed compilation/
tests, physical holdout isolation, custody release, tape/backtest assembly,
statistical acceptance and all promotion/production gates remain open.

# ADR 0071: Pinned strategy image handoff to the fixed engine

## Status

Implemented and tested with bounded archive fixtures and simulated daemon metadata.
The actual read-only probe refuses the missing Docker Linux endpoint. Live import,
image inspection, kernel confinement and candidate execution remain unverified.

## Decision

Verify a single immutable archive byte snapshot against independent worker/base/
archive/manifest/config pins before any daemon call. Send those same bytes once
for an explicitly requested load, then inspect the pinned platform/manifest.
Provide a separate read-only presence operation that never invokes load.

Reuse the public fixed-endpoint bounded transport and engine resource predicate
from the existing repair infrastructure. Require the containerd image store and
platform-inspection API; compare loaded descriptor, complete expanded layer
inventory and fixed runtime settings against the verified archive. Unknown or
active legacy runtime settings require review and are refused by default.

Keep all execution/promotion flags false. A nested archive receipt remains byte
integrity evidence. Engine/image metadata hashes bind observed data, without
creating a kernel/source certificate. No candidate files are staged or executed.

## Consequences

Replacing the archive path during a handoff cannot replace the already verified
snapshot. Unknown import outcomes retain inputs and never trigger an automatic
retry, shared-image removal or container action. The CLI emits generic errors and
does not echo daemon details. Existing static archive identity, schemas, worker,
older repair paths and strategy closures remain unchanged.

The future execution parent still needs original-package request binding, staging
fencing, full container admission, owned create/start/deadline/kill/remove/recovery,
actual Linux enforcement and independent behavior/source attestation. Proposed
no-holdout compilation/tests, statistical acceptance and production gates remain.
Rollback withdraws these handoff commands; it does not delete shared Docker images.
See `docs/STRATEGY_IMAGE_HANDOFF.md` for exact commands and references.

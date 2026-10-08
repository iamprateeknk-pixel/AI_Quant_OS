# 0088: Retain uncertain validation as a historical audited observation

Date: 2026-10-08

## Decision

Expose an immutable metadata-only interruption contract bound to the original
package, candidate, job and exact audited stage/checkpoint. Capture only an
unacknowledged stage; keep the declared boundary separate from physical worker
failure classification. Exclude trading statistics, raw exceptions and all
cleanup/acceptance authority. Use exclusive canonical operator files with independent
byte pins. Verify the original checkpoint against retained audit history even after
later pinned reconciliation.

Reuse the existing owned-job reader and audit chain. Add read-only checkpoint access
to the stage store without changing its wire models or transitions. Keep A10 and the
existing scorecard/failure archive unchanged. No worker, cleanup, numeric evaluation,
source import or retry is added to interruption capture or verification.

## Scope

These reports retain local stage uncertainty. Physical failure classification,
independent attestation/isolation, full acceptance, missions and production gates
remain required. See VALIDATION_INTERRUPTION_HANDOFF.md for commands and limitations.

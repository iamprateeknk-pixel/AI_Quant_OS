# 0160 — Persist unavailable local monitoring evaluations

## Decision

Retain a source-level episode when a protected local Prometheus rules read is
unavailable. Keep it separate from the two selected scrape rules. The first
unavailable read opens a pending episode; repeated unavailable reads
can reach firing after the existing 120-second development hold. A disabled
read marks an existing episode not currently observed but does not create an
episode on its own. A later valid rules response records it as observed
inactive. A later outage after recovery opens a new episode.

The episode name and runbook describe rule-evaluation source availability. They
do not assert that the Prometheus process exited; endpoint, network, timeout,
HTTP, parsing, and configuration failures share the unavailable result. No
process supervision, external notification, automated control, or recovery
authority is added. Keep the 0158 receipt schema and 0159 page schema unchanged;
publish the expanded page contract additively.

## Evidence and limits

Tests cover pending-to-firing timing, disablement, recovery, recurrence,
restart persistence, protected API capture and review receipt denial of control
authority. Frontend selection accepts only the fixed new rule name and runbook.
Browser visual acceptance and independent process liveness remain open.

## Rollback

Revert the source-availability journal event, API projection, frontend decoder
and row label, additive page schema, runbook, tests and handoff. The original
two scrape-rule episodes and review receipts remain supported.

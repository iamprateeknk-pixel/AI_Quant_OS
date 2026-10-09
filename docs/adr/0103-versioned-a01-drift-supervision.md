# ADR 0103: Versioned paper supervisor with A14 child evidence

Date: 2026-10-09

## Decision

Register A01 0.3.0 as an immutable PARTIAL/SHADOW version with the original fixed
capabilities and parent deadline. Add A14 0.2.0 to its bounded seven-child dispatch
contract, while preserving original manifests and the six-child 0.2.0 TaskPlan
schema. Reuse existing paper context capture, durable quotas and graph receipts.
Require exact parent version, catalog/source permissions and child identity.

Immediately pass retained child receipts to enabled health monitoring before
parent completion; existing receipt scans continue to provide replay coverage.
Failed/uncertain observations remain subject to existing quarantine/safety policy.

## Consequences

The new supervisor records original source-bound paper drift evidence and missing
inputs through the actual graph. It cannot claim calibration, causal certainty,
demotion, live readiness or capital authority. Child budgets and deadlines can
withhold drift; retries reuse receipts and preserve uncertain reservations.

Catalog cardinality grows from 30 to 31. Original supervisor contexts still treat
the new version as unsupported, and existing stored catalogs/receipts remain
archival. Both wire schemas are separately retained. Dynamic task routing, full
missions and independent promotion/release acceptance remain pending.

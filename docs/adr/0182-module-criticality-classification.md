# Classify package modules by paper authority criticality

## Decision

Extend the machine-readable architecture inventory with a complete, disjoint critical/supporting classification for all 323 current Python package modules. Critical modules directly enforce or compose paper order/risk authority, operator controls, broker/agent admission, or durable order/safety/audit state. Supporting modules are not deterministic authority owners in that boundary.

## Consequences

- Adding an inventoried package module now requires assigning it to one criticality group.
- Supporting does not mean low-risk: those modules may affect inputs, evidence, privacy, availability, or presentation.
- The classification describes local paper authority and does not designate a live-release owner.
- Architecture layering, transitive call-graph proof, threat review, and independent critical review remain open.

## Verification

- `tests/test_architecture_broker_boundary.py`: 7 passed in a fresh archive checkout with the changed architecture file and test overlaid.
- The classification test verifies no overlap, complete coverage, and representative core/supporting assignments.

## Rollback

Revert the classification, parity test, handoff/status notes, and ADR together. No runtime behavior changes.
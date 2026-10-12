# ADR 0171: A15 reviews retained counterfactual reports without rerunning them

## Decision

Register A15 0.2.0 as a PARTIAL/SHADOW paper graph mission. Use the task-bound replay ID to load a verified existing `CounterfactualStore` report and project bounded alternative PnL differences into a typed advisory envelope. Mark every alternative as simulated on already-seen data, report disconnected decision/fill/world-state lineage, and return WAIT_FOR_EVIDENCE when the report is absent or incomplete.

## Consequences

The mission makes existing P09 diagnostics reviewable through the agent runtime without executing candidate source or changing replay reservations. It does not create new simulations, reconstruct full trade/world lineage, replace validation, or authorize promotion. Independent A15 acceptance and all remaining P00–P13 gates stay open.

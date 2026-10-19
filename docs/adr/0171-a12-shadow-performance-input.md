# ADR 0171: A12 shadow performance input

## Decision

Keep A12 0.2.0's unavailable-metric contract immutable and add A12 0.3.0 for the new candidate-keyed shadow-performance input. This source reads only assignments and reports returned by the verified `ShadowStore` API. It exposes the latest report per assignment, including its candidate and assignment identities, sequence, outcome, quote currency, cumulative net P&L, and report fingerprint.

The source is capped at 100 assignments. Missing stores, empty histories, failed reports, and over-capacity inventories remain explicit. It never silently truncates history or combines P&L across currencies or assignments. A12 0.3.0 fingerprints the captured performance observation and rechecks it at execution through the existing task-bound read capability; a changed snapshot invalidates the graph call.

## Safety boundary

These values are descriptive shadow evidence. They do not define a comparable performance score or qualify a candidate. A12 0.3.0 keeps `performance_available=false`, emits no eligible strategies, and leaves drift unassessed. Candidate approval, validated lifecycle, regime and portfolio constraints, performance qualification, drift thresholds, calibration, and independent acceptance remain required. The agent remains PARTIAL/SHADOW and cannot place orders, modify candidate lifecycle, or promote.

## Compatibility

The 0.2.0 manifest, source payload, result schema, and behavior remain available. Version 0.3.0 has its own manifest and output schema identity. The registry read handler checks the lifecycle and tool permission for the invoked version and binds the shadow fingerprint only for 0.3.0.

## Verification

The focused acceptance evidence is retained in `docs/evaluations/strategy-selector-shadow-0171.json`. Full project and P13 gates remain open.

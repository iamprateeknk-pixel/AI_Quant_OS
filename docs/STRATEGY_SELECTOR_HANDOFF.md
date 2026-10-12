# A12 Strategy Selector handoff

## Current state

A12 0.2.0 is PARTIAL/SHADOW and runs only in the paper agent graph. It compiles a bounded snapshot of the research candidate registry and current paper WorldState, then verifies the registry has not changed before returning an advisory result. It does not create orders, modify lifecycle state, or promote a candidate.

The current CandidateStore has no approved lifecycle state and its retained research outcome does not establish validated strategy eligibility. Candidate keyed performance and drift feeds are also not connected to the selector. The output therefore contains no eligible strategies and reports the missing evidence explicitly. It may show candidate exclusion reasons for the observed registry rows.

## Implemented surface

- Protected generic context and execution routes for A12 0.2.0.
- Four source-bound inputs: approved strategy registry snapshot, WorldState, candidate keyed performance, and candidate keyed drift.
- The performance and drift slots report `UNCONNECTED` and `UNASSESSED`; they do not infer metrics.
- A task-bound `STRATEGY_REGISTRY_READ` handler recomputes and checks the exact registry fingerprint at execution.
- Output is deterministic, confidence zero, uncertainty one, and `promotion_allowed: false`.

## Acceptance evidence

The focused locked Windows suite covers selector schema/evidence checks, protected API execution, operator authorization, registry change after context compilation, catalog registration, context authorization and expiry, graph execution, tool budgets, architecture boundary, and neighboring A10/A11 graph paths. See `docs/evaluations/strategy-selector-0169.json`.

## Remaining work

This mission does not rank candidates. Completion requires a reviewed approved-strategy lifecycle and validated-state contract, candidate keyed shadow performance and drift sources, regime/portfolio constraint evidence, measured quality and calibration, model routing policy where appropriate, independent acceptance datasets, and full P00–P13 release gates. Until those are implemented and independently checked, A12 must remain PARTIAL/SHADOW and withhold eligibility.

# A12 Strategy Selector handoff

## Current state

A12 0.2.0 remains a PARTIAL/SHADOW compatibility version with explicit unavailable performance and drift inputs. A12 0.3.0 adds candidate-keyed, read-only diagnostics from verified forward-shadow assignments and their latest retained reports. It fingerprints the performance snapshot and rechecks that exact snapshot, along with the registry, when the graph executes. Neither version creates orders, modifies lifecycle state, or promotes a candidate.

The current CandidateStore has no approved lifecycle state and its retained research outcome does not establish validated strategy eligibility. Version 0.3.0 can expose the latest cumulative shadow P&L, currency, sequence, outcome, and report fingerprint for at most 100 assignments. This is descriptive evidence only: it is not normalized across candidates, quality-qualified, or drift-assessed. Missing stores and over-capacity inventories are explicit; rows are never silently truncated. The output therefore contains no eligible strategies and reports the missing qualification and drift evidence.

## Implemented surface

- Protected generic context and execution routes for A12 0.2.0 and 0.3.0.
- Four source-bound inputs: approved strategy registry snapshot, WorldState, candidate-keyed performance, and candidate-keyed drift.
- Version 0.2.0 preserves `UNCONNECTED` and `UNASSESSED`; version 0.3.0 reads verified shadow-store history and keeps drift `UNASSESSED`.
- Shadow-history snapshot creation and execution-time fingerprint rechecks run in worker threads so SQLite verification cannot block the async runtime. The A12 0.3.0 context route allows up to its two-second mission deadline; the ordinary per-source deadline still converts a slow read into a `TIMEOUT` input.
- The task-bound `STRATEGY_REGISTRY_READ` handler rechecks the exact registry and, for 0.3.0, shadow-performance fingerprints at execution under that exact manifest’s lifecycle.
- Both outputs remain deterministic, confidence zero, uncertainty one, empty of eligible strategies, and `promotion_allowed: false`.

## Acceptance evidence

The focused locked Windows suite covers both versions, verified forward-shadow metric projection, performance changes after context compilation, own-version quarantine, protected API execution, operator authorization, registry change after context compilation, context authorization and expiry, graph execution, tool budgets, architecture boundary, and neighboring A10/A11 graph paths. See `docs/evaluations/strategy-selector-shadow-0171.json`.

## Remaining work

This mission still does not rank candidates. Completion requires a reviewed approved-strategy lifecycle and validated-state contract, measured performance qualification, drift thresholds and attribution, regime/portfolio constraint evidence, quality calibration, model routing policy where appropriate, independent acceptance datasets, and full P00–P13 release gates. Until those are implemented and independently checked, A12 must remain PARTIAL/SHADOW and withhold eligibility.

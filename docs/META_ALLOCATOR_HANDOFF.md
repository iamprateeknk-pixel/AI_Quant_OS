# A13 Meta Allocator handoff

## Current state

A13 0.2.0 is PARTIAL/SHADOW in the paper graph. It creates a deterministic advisory envelope for allocation review and returns no allocation weights while required evidence remains incomplete. A13 0.3.0 adds an explicit A12 0.3.0 graph-run dependency: the operator names one upstream run, and context compilation accepts it only when its retained receipt and typed output are complete, current, and provenance-valid. The A13 context binds the upstream run ID, context reference, output digest, selector state, candidate IDs, registry digest, and blockers. Neither version places orders, changes risk limits, activates strategies, or promotes candidates.

## Evidence and blockers

- A13 0.2.0 does not consume an upstream A12 graph output. A13 0.3.0 consumes one explicit, current A12 0.3.0 receipt and rejects missing, stale, incomplete, or wrong-version runs.
- The portfolio projection records one aggregate exposure snapshot and explicitly labels its coverage as single-instrument.
- The current risk limits and safety state are captured, but the allocation risk budget is uncalibrated.
- No validated candidate return matrix is connected for covariance.
- Exposure evidence is limited to the current portfolio snapshot and cannot establish full cross-instrument concentration.

Both versions remain `WAIT_FOR_EVIDENCE`, with an empty allocation list, zero confidence, full uncertainty, and both `execution_authorized` and `promotion_allowed` false. Version 0.3.0 records the exact selector receipt and retains upstream blockers. Covariance, calibrated risk budget, and full cross-instrument exposure remain unavailable. The deterministic risk kernel remains authoritative for each order decision.

## Acceptance evidence

The focused suite checks the exact input schema, source/time binding, evidence-withholding outputs, registry catalog, context/runtime behavior, and neighboring A10–A12 graph paths. The 0.3.0 path executes a real A12 graph run, binds its output digest, and rejects a missing or wrong-version upstream run. See `docs/evaluations/meta-allocator-0170.json` and `docs/evaluations/meta-allocator-0207.json`.

## Remaining work

A13 0.3.0 closes the graph-wiring gap but does not establish an eligible strategy set: current A12 outputs remain blocked pending performance qualification, drift, and approval evidence. Full portfolio, calibrated risk-budget, and cross-instrument exposure evidence plus an independently reviewed candidate covariance matrix remain absent. Add independent allocation acceptance cases, quantify concentration uncertainty, and complete the remaining P00–P13 gates. A13 remains PARTIAL/SHADOW until those checks pass.

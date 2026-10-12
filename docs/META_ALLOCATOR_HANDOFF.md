# A13 Meta Allocator handoff

## Current state

A13 0.2.0 is PARTIAL/SHADOW in the paper graph. It creates a deterministic advisory envelope for allocation review and returns no allocation weights while required evidence remains incomplete. It does not place orders, change risk limits, activate strategies, or promote candidates.

## Evidence and blockers

- The upstream A12 strategy-set output is not connected to A13 graph execution.
- The portfolio projection records one aggregate exposure snapshot and explicitly labels its coverage as single-instrument.
- The current risk limits and safety state are captured, but the allocation risk budget is uncalibrated.
- No validated candidate return matrix is connected for covariance.
- Exposure evidence is limited to the current portfolio snapshot and cannot establish full cross-instrument concentration.

The output is always `WAIT_FOR_EVIDENCE`, with an empty allocation list, zero confidence, full uncertainty, and both `execution_authorized` and `promotion_allowed` false. The existing deterministic risk kernel remains authoritative for each order decision.

## Acceptance evidence

The focused locked Windows suite checks the exact input schema, source/time binding, the evidence-withholding output, registry catalog, context/runtime behavior, and neighboring A10–A12 graph paths. See `docs/evaluations/meta-allocator-0170.json`.

## Remaining work

Connect a validated A12 strategy set; full portfolio, risk-budget, and cross-instrument exposure evidence; and an independently reviewed candidate covariance matrix. Then add independent allocation acceptance cases, quantify concentration uncertainty, and complete the remaining P00–P13 gates. A13 remains PARTIAL/SHADOW until those checks pass.

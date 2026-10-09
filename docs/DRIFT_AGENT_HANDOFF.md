# A14 paper execution-drift mission

A14 version 0.2.0 is now PARTIAL/SHADOW in the real context and graph runtime.
Its original 0.1.0 manifest remains PLANNED. The existing protected paper agent
context/execute/archive/resume routes serve it; execution creates no orders or
demotion decisions. The A01 supervisor's six-specialist dispatch set is unchanged;
A14 remains explicitly deferred as unsupported by that particular wake plan.

## Observations and comparison

The server captures the original verified paper order owners, fills and initial
Risk Kernel decision-price benchmarks atomically. The same ledger identity binds
five source records: fills, version inventory and explicit unconnected live,
shadow and prediction observations. Every original source identity, capability,
resource class, time, payload and content reference is checked during output
acceptance. The comparison is recomputed from the retained ledger. Removing
mandatory evidence gaps or substituting a result is refused.

A declared policy specifies adjacent UTC windows, minimum fills in each window
and an adverse cost-increase threshold. Compare each strategy/version/instrument
independently; do not combine currencies or versions. Baseline is
`[as_of - 2 * window, as_of - window)`, current is
`[as_of - window, as_of]`. Fills outside those windows do not contribute, while
the full supplied ledger is still validated.

Each window divides spread cost, signed decision-side-to-fill price movement and
fees by gross initial decision-midpoint notional, in basis points. Buy/sell signs
remain consistent. Each component is explicitly rounded to six decimal places
with half-even rounding under a fixed decimal context; total cost is the sum of
those displayed components. Deltas use those declared rounded measurements.
Changed raw cash flow or position size alone is not a PnL optimization signal.
These are descriptive weighted costs, not causal attribution or a statistical test.

Missing benchmarks withhold affected window costs and all deltas. Insufficient
baseline/current samples withhold the comparison. An adverse positive increase at
or above the declared threshold yields COST_INCREASE and HUMAN_REVIEW_ONLY.
Other complete comparisons say NO_COST_INCREASE, which does not assert no drift
in signal, regime, sizing or data. Automatic demotion remains unassessed.

## Configuration and limits

`create_app(drift_policy=DriftPolicy(...))` accepts a typed declaration. Otherwise
`AI_QUANT_DRIFT_POLICY` may name a local JSON file with exactly `window_seconds`,
`minimum_fills` and decimal-text `adverse_cost_increase_bps`. Files are limited to
4096 bytes; duplicate fields, booleans, non-finite/fine-grained thresholds and
out-of-range values are refused. Window bounds are 60..86400 seconds, minimum
fills 2..512, threshold 0..10000 bps with at most six decimal places.

No policy is configured by default: output explicitly says POLICY_UNCONFIGURED.
The illustrative 3600-second/two-fill/ten-bps declaration in the development
snapshot is a test configuration, not a calibrated or recommended trading limit.
The complete declaration is retained in each context and bound by its fingerprint.
There is no policy-edit HTTP endpoint or threshold inferred from future outcomes.

Snapshots allow at most 512 owners, fills and benchmarks, and 128 comparison
groups. Existing evidence/context byte bounds apply without clipping originals.
Capacity or source-integrity failures withhold context. The five declared fetches
share durable role call quotas; pre-compilation capture retains ADR 0100's limits.

## Verification and remaining work

Failing math/module, API/runtime and missing-artifact checks preceded implementation.
Checks cover normalized cost changes, buy/sell signs, quantity changes, exact UTC
boundaries, separate versions, legacy missing prices, insufficient samples, malformed
policy, exact schema and retained synthetic recomputation. Actual API/graph checks
cover a retained paper fill, original-price corruption, context/output binding,
missing feeds, original receipts after restart, quarantine and typed failed/timed-out
health events. Evidence is recorded in `evaluations/drift-agent-0101.json`.

Confidence is zero, uncertainty one and escalation mandatory. Actual live/shadow
results and predictions are unconnected. Signal/regime/sizing/data causal analysis,
statistical/held-out detection, calibration, approved baselines, scheduled monitoring,
A01 integration, demotion recommendations/workflows, independent review and all
promotion/live gates remain pending. P00-P13 remains incomplete. No paid provider,
live broker, candidate execution or automatic trading control was used.

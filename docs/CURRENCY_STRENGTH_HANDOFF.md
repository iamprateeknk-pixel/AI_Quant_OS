# A04 Currency Strength Agent

The versioned paper graph now executes A04 0.2.0 and retains its context, typed
`CurrencyScoreVector`, manifest, receipt and audit binding. The original 0.1.0
planned manifest remains unchanged. The new version is `PARTIAL` and `SHADOW`;
it has the same fixed read-only tool ceiling and no capital-control authority.

## Calculation and evidence

The server supplies currency twins, complete fresh bar histories, the current
paper regime labels, and an explicit fact that the macro source is unavailable.
Every used bar stream must reproduce its world-state history hash, instrument,
bar count and endpoint return. Observation times must be strictly increasing,
not future-dated, and identical across compared pairs. Missing/stale quote or
history evidence is excluded and disclosed; unavailable scores are null.

Prices are bounded to 1e-12..1e12 and 64 stored digits; each history has
3..64 bars. The current application has four configured pairs. Source/model
contracts additionally cap instruments and currencies at 16; the mathematical
network caps edges at 32. The A04 context limits are 65,536 bytes per source and
128,000 bytes total, enforced by the existing permission-checked compiler. Other
missions retain their existing budgets. The full 64-bar, four-pair case is tested.

The method uses cumulative natural-log returns multiplied by 10,000. A sorted,
deterministic spanning forest selects only n-1 pair factors in a connected group
of n currencies. Scores are centered within that group. Each currency retains
its signed coefficients and contributions from those factors. Inverse/repeated
edges and cycles are retained as residual checks rather than additional score
weights. This handles algebraic redundancy; it does not establish statistical
independence or calibrated confidence.

Every observation in every redundant edge is compared with its implied currency
path. Residuals above 0.00000001 log-return bps withhold the affected component.
Endpoint agreement cannot hide a contradictory intermediate observation.
Unaligned histories withhold all comparisons. Disconnected components have no
common ranking; isolated currencies have no score. Scores are relative price
movements, not expected returns, macro bias, trading recommendations or positions.

Macro/calibration evidence remains unavailable, confidence is constrained to zero,
uncertainty is one, and escalation is mandatory. Fabricated nonzero confidence
is rejected by the runtime's output validation. The execution core remains
independent of these advisory values. Quarantine and expiry admission use the
existing current registry/context checks.

## Use

Use the existing operator-protected context/execute/read routes with agent `A04`,
version `0.2.0`. The durable paper API must be configured. No new operator or
activation route is added.

```text
POST /api/v1/paper/agents/context
{"agent_id":"A04","agent_version":"0.2.0","task_id":"currency-observation"}

POST /api/v1/paper/agents/execute
{"run_id":"currency-observation-1","context_ref":"<returned context_ref>"}

GET /api/v1/agents/graph-runs/currency-observation-1
```

The output schema is `schemas/currency-score-vector.schema.json`. Decimal values
use fixed-point strings so even tiny residuals satisfy the exported schema.

## Synthetic baseline

```text
python -m ai_quant_os.currency_strength_evaluation tests/fixtures/currency_strength_baseline.json --output <new-report-path>
```

The command validates a bounded JSON recipe, runs the real persisted graph and
records expected/actual results, context references, measured graph invocation
duration, fixture SHA-256, three mission-file hashes and evaluator hash. It refuses
to overwrite an existing report and exits nonzero on a mismatch. The source hashes
cover the named mission files, not the full transitive runtime or environment.

The retained six-case report is `evaluations/currency-strength-baseline.json`:
coherent triangle, flat values, intermediate contradiction, missing data,
disconnected coverage and unaligned histories. All six match their independently
declared scores/status/rank within 1e-8 log-return bps. Schema and typed validation
pass for every recorded output. This is synthetic development evidence, not
held-out accuracy, empirical calibration, production latency or promotion evidence.

An initial uncommitted report exposed scientific-notation/schema incompatibility.
Its exact report and source/fixture bytes remain under ignored
`data-local/currency-strength-pre-schema-0056`; the canonical report was regenerated
after the fixed-point serializer passed the regression. Mission/fixture/report
files keep LF across checkouts to preserve their byte pins.

## Remaining gates

Real macro evidence, broader universe/horizon policy, independently reviewed
dependence/calibration evaluation, historical and prospective shadow acceptance,
full SDK/model routing and production source/deployment evidence remain.
The planned original manifest is not an executable rollback implementation.
A04 is not added to the existing A02/A07 repair/genome/evolution source catalogs;
that integration needs its own owned-source and immutable protocol review.
No promotion or live-capital gate is satisfied by this increment.

## Final local verification

The final locked Windows regression completed with **1,060 passed and 15 skipped
in 870.34 seconds**, with actual built UI integration required. The final focused
agent/context/graph suite passed 182 tests; all 219 Python files satisfy formatting
and lint passes. Both dependency locks still reproduce. Retained native artifacts,
A02/A07 genome lineage and prospective protocol exports remain compatible. A fresh
Windows index checkout preserves all mission, evaluator and fixture byte pins.
Linux/hosted CI and the remaining promotion gates are unverified.

# A05 Volatility and Liquidity Agent

A05 0.2.0 now runs in the persisted paper graph. Its manifest remains `PARTIAL`
and `SHADOW`, with the original read-only role ceiling. All eighteen original
0.1.0 planned manifests remain unchanged. No order approval comes from A05.

## Measurements

The server supplies raw quotes, complete bounded bar histories, checked spreads,
and explicit unavailable session/broker telemetry facts. Quote and bar hashes,
instrument coverage, counts, freshness and world version must agree with the
parent world snapshot. Changing or duplicating a source rejects verification.

The pure calculation uses natural-log close returns multiplied by 10,000,
their arithmetic mean and population standard deviation. Values describe
returns per observation; they are not annualized volatility. History requires
at least three bars, and the latest-return abnormality feature requires four.
The latest return is compared with preceding returns, excluding itself from
the baseline. For a nonzero baseline deviation, z is the absolute deviation
divided by baseline standard deviation and the feature is z/(1+z). This is an
uncalibrated feature, not an event probability or trading threshold.

Irregular observation cadence withholds that comparison. Zero variance with
unchanged returns yields zero; a change against a zero-variance baseline
withholds the score. Missing/short histories produce null measurements. Stale
history can retain descriptive statistics with its historical window and stale
status disclosed; it never becomes a usable current history.

Each history is capped at 64 bars, each price at 1e-12..1e12 and 64 stored digits.
Source contracts cap instruments at 16. The current application configures four.
Input and total context budgets are 65,536 and 160,000 bytes respectively. The
four-instrument, 64-bar case passes the real context compiler.

## Supplied paper L1 sizes

The new operator-protected endpoint accepts a quote plus displayed bid and ask
sizes in base currency units:

```text
POST /api/v1/paper/l1-quotes
{
  "quote": {
    "instrument_id": "EURUSD", "bid": "1.1000", "ask": "1.1002",
    "observed_at": "<current aware timestamp>", "world_state_version": "<paper version>"
  },
  "bid_size": "300", "ask_size": "100"
}
```

These are operator supplied synthetic observations, explicitly marked
`PAPER_OPERATOR_SYNTHETIC_L1`. Sizes are finite, nonnegative and at most 1e12,
with at most 64 stored digits and a stored exponent in -12..12. Invalid inputs
are rejected before publication. The new endpoint accepts configured paper
instruments only. The existing quote-only input contract remains compatible.

Sizes bind to the exact quote hash and timestamp. A quote-only update invalidates
its sizes; the source publisher excludes any size binding that no longer matches
the captured quote. Only fresh quotes expose usable sizes. Reported imbalance is
(bid size - ask size)/(bid size + ask size); both sizes zero yields null. Candle
volume never supplies an inferred quote size. No executable depth, fillability,
market impact, full order book or actual broker telemetry is established.

Quotes and sizes are process-local. A restart does not restore them as fresh;
persisted contexts and receipts retain their exact original observations.

## Execute and read

Use the operator-protected existing graph routes with agent `A05`, version
`0.2.0`. The durable paper API must be configured:

```text
POST /api/v1/paper/agents/context
{"agent_id":"A05","agent_version":"0.2.0","task_id":"liquidity-observation"}

POST /api/v1/paper/agents/execute
{"run_id":"liquidity-observation-1","context_ref":"<returned context_ref>"}

GET /api/v1/agents/graph-runs/liquidity-observation-1
```

The output schema is `schemas/liquidity-state.schema.json`. Decimal measurements
use fixed-point strings. Confidence is constrained to zero, uncertainty is one,
and escalation is mandatory. Every assessment has `may_add_risk=false` and
tradeability `UNVERIFIED`; hard-gate authority is `RISK_KERNEL`. The actual paper
order gateway rejects stale quotes and excessive per-intent spreads both before
and after an A05 run. No agent or synthetic size overrides these checks.

## Retained development baseline

```text
python -m ai_quant_os.liquidity_evaluation tests/fixtures/liquidity_baseline.json --output <new-report-path>
```

The bounded JSON recipe executes the actual persisted graph and compares declared
expectations. The command refuses report overwrite and exits nonzero on a mismatch.
`evaluations/liquidity-baseline.json` retains six matching cases: alternating
returns with supplied sizes, flat prices with empty sizes, a degenerate change,
stale quote with supplied sizes, missing data, and short history. All outputs
pass typed and JSON Schema validation. Reports retain the recipe hash, three
named mission source hashes, evaluator hash, context references and measured
invocation duration. These hashes do not cover the full transitive runtime,
API composition or environment. LF attributes preserve named byte pins.

This is synthetic development evidence. Held-out accuracy, calibrated thresholds,
real-session/liquidity feeds, production latency and promotion remain unverified.
A05 is not added to the existing A02/A07 repair or genome source catalogs. All
broader execution-pack and live-release gates remain open.

## Rollback

Stop the owned local application, preserve its state directory and retained
artifacts, then revert the A05 increment. There is no database migration. Older
code lacks the A05 0.2.0 registry/implementation and may be unable to validate or
serve its archived graph receipts; preserve those bytes for a compatible reader.
The planned 0.1.0 manifest is not an executable rollback. No open orders or safety
state need changes for this advisory-only increment.

## Local verification

1,091 tests passed and 15 skipped in 973.63 seconds with the actual built
frontend integration required. The 149 focused checks passed in 16.20 seconds.
Lint and formatting checks passed for all 227 Python files; dependency consistency
and both 96-package lock profiles passed. Retained A04/A05 source/fixture hashes,
output schemas, four sealed strategy source inventories and prior artifact/genome
compatibility checks passed. A fresh index checkout preserved all named byte pins.
Automatic architecture checking remains unavailable because its declaration is
missing; manual import review passed. Hosted/Linux/production verification is open.

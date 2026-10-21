# Edge Sprint 1 — Results

**Date:** 2026-10-21  
**Scope:** pre-registered hypotheses from `src/ai_quant_os/edge_hypotheses.py`, evaluated once through the Validation Fortress (`src/ai_quant_os/validation_fortress.py`, default thresholds). No parameter was tuned after seeing results; a FAIL is a rejection under the hypothesis's own falsification criterion.

## Data

Source: FRED (Federal Reserve Economic Data), daily noon buying rates, fetched from https://fred.stlouisfed.org/graph/fredgraph.csv starting 2015-01-01. EURUSD: live FRED DEXUSEU; EURUSD: cache write not permitted in this environment; GBPUSD: live FRED DEXUSUK; GBPUSD: cache write not permitted in this environment; USDJPY: live FRED DEXJPUS; USDJPY: cache write not permitted in this environment; EURJPY: derived as EURUSD x USDJPY on common dates.

| Instrument | FRED series | Bars | First | Last | SHA-256 of canonical CSV |
|---|---|---:|---|---|---|
| EURUSD | DEXUSEU | 2947 | 2015-01-02 | 2026-10-16 | `2a7660c459d7f6c5…` |
| GBPUSD | DEXUSUK | 2947 | 2015-01-02 | 2026-10-16 | `0b1be3a9abfa8d5d…` |
| USDJPY | DEXJPUS | 2947 | 2015-01-02 | 2026-10-16 | `5c255b25d2c61d9a…` |
| EURJPY | derived EURUSD x USDJPY | 2947 | 2015-01-02 | 2026-10-16 | `95c52153408c013d…` |

Limitations, stated plainly: FRED noon rates are reference (mid) rates, not tradable OHLC — every bar here has open = high = low = close, so range-based daily logic (M03) degenerates to close-based breakouts. FRED observation dates are used as bar dates. Policy rates for carry are observation-dated and forward-filled; publication lags are ignored. INR pairs: **PENDING_DATA** — no free reliable USDINR reference series was obtainable, and none was fabricated.

Carry rate data: available.

## Cost model (modelled, not observed)

One-way cost per 1.0 unit of position change (spread + slippage allowance), charged in the first period the new position is held. Fees are zero (spread-only retail spot convention). The fortress additionally requires the strategy to stay profitable at **2x** these costs (COST_STRESS_2X gate).

| Instrument | One-way cost | In pips |
|---|---:|---:|
| EURUSD | 8e-05 | 0.8 |
| GBPUSD | 0.0001 | 1.0 |
| USDJPY | 0.008 | 0.8 |
| EURJPY | 0.01 | 1.0 |

## Fortress gates (defaults, unchanged)

DATA_INTEGRITY · SAMPLE_SIZE (>= 100 trades, >= 240 periods) · EXPECTANCY · WALK_FORWARD (5 folds, >= 60% positive) · HOLDOUT (final 25%) · CSCV_PBO (<= 0.50) · DEFLATED_SHARPE (>= 0.95, corrected for all trials in this sprint) · COST_STRESS_2X. Verdict PASS requires every gate to pass.

## Headline result

**0 of 24 hypothesis/instrument evaluations passed the fortress.** 8 hypotheses were not evaluated (see below). A low pass count is the expected outcome of an honest sprint — the roadmap's base case is that most hypotheses die here.

## Results by evaluation

| Hypothesis | Instrument | Trades | Net return* | Ann. Sharpe | PBO | DSR | Stressed net* | Verdict | Failed gates |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| M01 TimeSeriesMomentum | EURUSD | 210 | -11.12% | -0.13 | 0.56 | 0.02 | -14.11% | FAIL | EXPECTANCY, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| M01 TimeSeriesMomentum | GBPUSD | 176 | +12.76% | 0.12 | 0.37 | 0.10 | +10.11% | FAIL | HOLDOUT, DEFLATED_SHARPE |
| M01 TimeSeriesMomentum | USDJPY | 139 | +44.12% | 0.41 | 0.80 | 0.39 | +42.27% | FAIL | HOLDOUT, CSCV_PBO, DEFLATED_SHARPE |
| M01 TimeSeriesMomentum | EURJPY | 195 | -9.48% | -0.09 | 0.20 | 0.02 | -12.27% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| M02 MovingAverageCrossTrend | EURUSD | 46 | -17.15% | -0.20 | 0.11 | 0.01 | -17.80% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| M02 MovingAverageCrossTrend | GBPUSD | 32 | +24.41% | 0.23 | 0.37 | 0.19 | +23.93% | FAIL | SAMPLE_SIZE, HOLDOUT, DEFLATED_SHARPE |
| M02 MovingAverageCrossTrend | USDJPY | 30 | +11.11% | 0.10 | 0.57 | 0.09 | +10.72% | FAIL | SAMPLE_SIZE, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE |
| M02 MovingAverageCrossTrend | EURJPY | 36 | -12.32% | -0.12 | 0.19 | 0.02 | -12.84% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| M03 DonchianBreakout | EURUSD | 59 | -27.40% | -0.39 | 0.31 | 0.00 | -28.24% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| M03 DonchianBreakout | GBPUSD | 54 | -18.72% | -0.22 | 0.00 | 0.01 | -19.53% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| M03 DonchianBreakout | USDJPY | 54 | -1.90% | -0.02 | 0.70 | 0.04 | -2.59% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| M03 DonchianBreakout | EURJPY | 56 | -18.22% | -0.22 | 0.24 | 0.01 | -19.03% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| M04 CarryDifferential | EURUSD | 1 | +19.75% | 0.22 | 0.93 | 0.17 | +19.75% | FAIL | SAMPLE_SIZE, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE |
| M04 CarryDifferential | GBPUSD | 27 | -22.81% | -0.30 | 0.61 | 0.00 | -23.23% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| M04 CarryDifferential | USDJPY | 4 | +33.36% | 0.37 | 0.37 | 0.35 | +33.31% | FAIL | SAMPLE_SIZE, WALK_FORWARD, DEFLATED_SHARPE |
| M04 CarryDifferential | EURJPY | 2 | +36.85% | 0.35 | 1.00 | 0.32 | +36.83% | FAIL | SAMPLE_SIZE, CSCV_PBO, DEFLATED_SHARPE |
| M05 CarryMomentumAgreement | EURUSD | 105 | -0.61% | -0.01 | 0.47 | 0.04 | -2.10% | FAIL | EXPECTANCY, DEFLATED_SHARPE, COST_STRESS_2X |
| M05 CarryMomentumAgreement | GBPUSD | 66 | -9.59% | -0.16 | 0.40 | 0.01 | -10.59% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| M05 CarryMomentumAgreement | USDJPY | 71 | +34.86% | 0.48 | 0.90 | 0.48 | +33.90% | FAIL | SAMPLE_SIZE, CSCV_PBO, DEFLATED_SHARPE |
| M05 CarryMomentumAgreement | EURJPY | 98 | +10.04% | 0.13 | 0.20 | 0.11 | +8.63% | FAIL | SAMPLE_SIZE, DEFLATED_SHARPE |
| M06 VolatilityManagedMomentum | EURUSD | 193 | +1.96% | 0.03 | 0.53 | 0.06 | -0.78% | FAIL | CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| M06 VolatilityManagedMomentum | GBPUSD | 170 | +16.88% | 0.20 | 0.63 | 0.16 | +14.29% | FAIL | HOLDOUT, CSCV_PBO, DEFLATED_SHARPE |
| M06 VolatilityManagedMomentum | USDJPY | 146 | +39.36% | 0.44 | 0.40 | 0.43 | +37.43% | FAIL | HOLDOUT, DEFLATED_SHARPE |
| M06 VolatilityManagedMomentum | EURJPY | 173 | +3.58% | 0.04 | 0.74 | 0.06 | +1.14% | FAIL | WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE |

\* Net return is the sum of per-period net returns (not compounded), the same quantity the fortress gates use.

## Gate detail

### M01 TimeSeriesMomentum — EURUSD

Mechanism: Slow-moving capital, hedging programmes and gradual information diffusion make FX trends persist for months; the other side is mean-reversion flow from exporters and reserve managers.

Falsifiable claim: The sign of the trailing 60-day return, held daily, delivers a net-of-cost Sharpe of at least 0.5 and a mean daily return of at least +1 bp across the four pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 2947.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.125 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | PASS | 0.015 | holdout profitable |
| CSCV_PBO | FAIL | 0.557 | PBO 0.557 above limit |
| DEFLATED_SHARPE | FAIL | 0.017 | DSR 0.0173 below minimum |
| COST_STRESS_2X | FAIL | -0.141 | doubling costs wipes out the edge |

### M01 TimeSeriesMomentum — GBPUSD

Mechanism: Slow-moving capital, hedging programmes and gradual information diffusion make FX trends persist for months; the other side is mean-reversion flow from exporters and reserve managers.

Falsifiable claim: The sign of the trailing 60-day return, held daily, delivers a net-of-cost Sharpe of at least 0.5 and a mean daily return of at least +1 bp across the four pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 2947.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.120 | positive expectancy |
| WALK_FORWARD | PASS | 0.800 | 0.800 of folds profitable |
| HOLDOUT | FAIL | -0.047 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.371 | PBO 0.371 within limit |
| DEFLATED_SHARPE | FAIL | 0.102 | DSR 0.1024 below minimum |
| COST_STRESS_2X | PASS | 0.101 | profitable at 2x costs |

### M01 TimeSeriesMomentum — USDJPY

Mechanism: Slow-moving capital, hedging programmes and gradual information diffusion make FX trends persist for months; the other side is mean-reversion flow from exporters and reserve managers.

Falsifiable claim: The sign of the trailing 60-day return, held daily, delivers a net-of-cost Sharpe of at least 0.5 and a mean daily return of at least +1 bp across the four pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 2947.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.411 | positive expectancy |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | FAIL | -0.003 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.800 | PBO 0.800 above limit |
| DEFLATED_SHARPE | FAIL | 0.391 | DSR 0.3912 below minimum |
| COST_STRESS_2X | PASS | 0.423 | profitable at 2x costs |

### M01 TimeSeriesMomentum — EURJPY

Mechanism: Slow-moving capital, hedging programmes and gradual information diffusion make FX trends persist for months; the other side is mean-reversion flow from exporters and reserve managers.

Falsifiable claim: The sign of the trailing 60-day return, held daily, delivers a net-of-cost Sharpe of at least 0.5 and a mean daily return of at least +1 bp across the four pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 2947.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.092 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.018 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.200 | PBO 0.200 within limit |
| DEFLATED_SHARPE | FAIL | 0.023 | DSR 0.0228 below minimum |
| COST_STRESS_2X | FAIL | -0.123 | doubling costs wipes out the edge |

### M02 MovingAverageCrossTrend — EURUSD

Mechanism: Dual moving-average crossovers are held by a large population of corporate and fund hedgers; their staggered execution sustains a move long enough for the cross follower to collect part of it.

Falsifiable claim: The 20/100-day crossover state machine earns a net-of-cost profit factor of at least 1.3 with a positive mean return per trade across the evaluation pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 46 < 100 |
| EXPECTANCY | FAIL | -0.197 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.033 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.114 | PBO 0.114 within limit |
| DEFLATED_SHARPE | FAIL | 0.009 | DSR 0.0091 below minimum |
| COST_STRESS_2X | FAIL | -0.178 | doubling costs wipes out the edge |

### M02 MovingAverageCrossTrend — GBPUSD

Mechanism: Dual moving-average crossovers are held by a large population of corporate and fund hedgers; their staggered execution sustains a move long enough for the cross follower to collect part of it.

Falsifiable claim: The 20/100-day crossover state machine earns a net-of-cost profit factor of at least 1.3 with a positive mean return per trade across the evaluation pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 32 < 100 |
| EXPECTANCY | PASS | 0.232 | positive expectancy |
| WALK_FORWARD | PASS | 0.800 | 0.800 of folds profitable |
| HOLDOUT | FAIL | -0.023 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.371 | PBO 0.371 within limit |
| DEFLATED_SHARPE | FAIL | 0.189 | DSR 0.1890 below minimum |
| COST_STRESS_2X | PASS | 0.239 | profitable at 2x costs |

### M02 MovingAverageCrossTrend — USDJPY

Mechanism: Dual moving-average crossovers are held by a large population of corporate and fund hedgers; their staggered execution sustains a move long enough for the cross follower to collect part of it.

Falsifiable claim: The 20/100-day crossover state machine earns a net-of-cost profit factor of at least 1.3 with a positive mean return per trade across the evaluation pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 30 < 100 |
| EXPECTANCY | PASS | 0.104 | positive expectancy |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.035 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.571 | PBO 0.571 above limit |
| DEFLATED_SHARPE | FAIL | 0.092 | DSR 0.0920 below minimum |
| COST_STRESS_2X | PASS | 0.107 | profitable at 2x costs |

### M02 MovingAverageCrossTrend — EURJPY

Mechanism: Dual moving-average crossovers are held by a large population of corporate and fund hedgers; their staggered execution sustains a move long enough for the cross follower to collect part of it.

Falsifiable claim: The 20/100-day crossover state machine earns a net-of-cost profit factor of at least 1.3 with a positive mean return per trade across the evaluation pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 36 < 100 |
| EXPECTANCY | FAIL | -0.120 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.005 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.186 | PBO 0.186 within limit |
| DEFLATED_SHARPE | FAIL | 0.018 | DSR 0.0180 below minimum |
| COST_STRESS_2X | FAIL | -0.128 | doubling costs wipes out the edge |

### M03 DonchianBreakout — EURUSD

Mechanism: A close beyond the 55-day extreme forces systematic trend funds and option-barrier hedgers to trade in the breakout direction; stops of the losing side add fuel, which the breakout trader harvests.

Falsifiable claim: The turtle-style state machine delivers a net-of-cost profit factor of at least 1.3, with at least 25 completed trades per pair and a positive mean return per trade.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 59 < 100 |
| EXPECTANCY | FAIL | -0.395 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.020 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.314 | PBO 0.314 within limit |
| DEFLATED_SHARPE | FAIL | 0.001 | DSR 0.0012 below minimum |
| COST_STRESS_2X | FAIL | -0.282 | doubling costs wipes out the edge |

### M03 DonchianBreakout — GBPUSD

Mechanism: A close beyond the 55-day extreme forces systematic trend funds and option-barrier hedgers to trade in the breakout direction; stops of the losing side add fuel, which the breakout trader harvests.

Falsifiable claim: The turtle-style state machine delivers a net-of-cost profit factor of at least 1.3, with at least 25 completed trades per pair and a positive mean return per trade.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 54 < 100 |
| EXPECTANCY | FAIL | -0.221 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.033 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.000 | PBO 0.000 within limit |
| DEFLATED_SHARPE | FAIL | 0.007 | DSR 0.0067 below minimum |
| COST_STRESS_2X | FAIL | -0.195 | doubling costs wipes out the edge |

### M03 DonchianBreakout — USDJPY

Mechanism: A close beyond the 55-day extreme forces systematic trend funds and option-barrier hedgers to trade in the breakout direction; stops of the losing side add fuel, which the breakout trader harvests.

Falsifiable claim: The turtle-style state machine delivers a net-of-cost profit factor of at least 1.3, with at least 25 completed trades per pair and a positive mean return per trade.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 54 < 100 |
| EXPECTANCY | FAIL | -0.022 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.033 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.700 | PBO 0.700 above limit |
| DEFLATED_SHARPE | FAIL | 0.039 | DSR 0.0392 below minimum |
| COST_STRESS_2X | FAIL | -0.026 | doubling costs wipes out the edge |

### M03 DonchianBreakout — EURJPY

Mechanism: A close beyond the 55-day extreme forces systematic trend funds and option-barrier hedgers to trade in the breakout direction; stops of the losing side add fuel, which the breakout trader harvests.

Falsifiable claim: The turtle-style state machine delivers a net-of-cost profit factor of at least 1.3, with at least 25 completed trades per pair and a positive mean return per trade.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 56 < 100 |
| EXPECTANCY | FAIL | -0.220 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.004 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.243 | PBO 0.243 within limit |
| DEFLATED_SHARPE | FAIL | 0.007 | DSR 0.0075 below minimum |
| COST_STRESS_2X | FAIL | -0.190 | doubling costs wipes out the edge |

### M04 CarryDifferential — EURUSD

Mechanism: Yield-seeking capital persistently buys the higher-rate currency; the other side is trade-flow and hedging demand that is insensitive to rates, so the differential is paid to whoever holds the position.

Falsifiable claim: Holding the sign of the rate differential beyond a 0.25% band earns the differential plus a net-of-cost total return of at least +1.0% annualised, with a Sharpe of at least 0.4.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 1 < 100 |
| EXPECTANCY | PASS | 0.217 | positive expectancy |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | FAIL | -0.001 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.929 | PBO 0.929 above limit |
| DEFLATED_SHARPE | FAIL | 0.174 | DSR 0.1739 below minimum |
| COST_STRESS_2X | PASS | 0.197 | profitable at 2x costs |

### M04 CarryDifferential — GBPUSD

Mechanism: Yield-seeking capital persistently buys the higher-rate currency; the other side is trade-flow and hedging demand that is insensitive to rates, so the differential is paid to whoever holds the position.

Falsifiable claim: Holding the sign of the rate differential beyond a 0.25% band earns the differential plus a net-of-cost total return of at least +1.0% annualised, with a Sharpe of at least 0.4.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 27 < 100 |
| EXPECTANCY | FAIL | -0.298 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.037 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.614 | PBO 0.614 above limit |
| DEFLATED_SHARPE | FAIL | 0.003 | DSR 0.0034 below minimum |
| COST_STRESS_2X | FAIL | -0.232 | doubling costs wipes out the edge |

### M04 CarryDifferential — USDJPY

Mechanism: Yield-seeking capital persistently buys the higher-rate currency; the other side is trade-flow and hedging demand that is insensitive to rates, so the differential is paid to whoever holds the position.

Falsifiable claim: Holding the sign of the rate differential beyond a 0.25% band earns the differential plus a net-of-cost total return of at least +1.0% annualised, with a Sharpe of at least 0.4.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 4 < 100 |
| EXPECTANCY | PASS | 0.374 | positive expectancy |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | PASS | 0.042 | holdout profitable |
| CSCV_PBO | PASS | 0.371 | PBO 0.371 within limit |
| DEFLATED_SHARPE | FAIL | 0.346 | DSR 0.3457 below minimum |
| COST_STRESS_2X | PASS | 0.333 | profitable at 2x costs |

### M04 CarryDifferential — EURJPY

Mechanism: Yield-seeking capital persistently buys the higher-rate currency; the other side is trade-flow and hedging demand that is insensitive to rates, so the differential is paid to whoever holds the position.

Falsifiable claim: Holding the sign of the rate differential beyond a 0.25% band earns the differential plus a net-of-cost total return of at least +1.0% annualised, with a Sharpe of at least 0.4.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 2 < 100 |
| EXPECTANCY | PASS | 0.351 | positive expectancy |
| WALK_FORWARD | PASS | 0.800 | 0.800 of folds profitable |
| HOLDOUT | PASS | 0.048 | holdout profitable |
| CSCV_PBO | FAIL | 1.000 | PBO 1.000 above limit |
| DEFLATED_SHARPE | FAIL | 0.316 | DSR 0.3155 below minimum |
| COST_STRESS_2X | PASS | 0.368 | profitable at 2x costs |

### M05 CarryMomentumAgreement — EURUSD

Mechanism: Carry performs worst exactly when momentum turns against it; requiring 60-day momentum to agree filters out the unwind phase while keeping the yield flow of aligned periods.

Falsifiable claim: The agreement-gated carry position beats ungated M04 by at least 0.2 Sharpe net of costs and keeps a net expectancy above 0, while being invested on at least 30% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 2947.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.009 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | PASS | 0.009 | holdout profitable |
| CSCV_PBO | PASS | 0.471 | PBO 0.471 within limit |
| DEFLATED_SHARPE | FAIL | 0.043 | DSR 0.0430 below minimum |
| COST_STRESS_2X | FAIL | -0.021 | doubling costs wipes out the edge |

### M05 CarryMomentumAgreement — GBPUSD

Mechanism: Carry performs worst exactly when momentum turns against it; requiring 60-day momentum to agree filters out the unwind phase while keeping the yield flow of aligned periods.

Falsifiable claim: The agreement-gated carry position beats ungated M04 by at least 0.2 Sharpe net of costs and keeps a net expectancy above 0, while being invested on at least 30% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 66 < 100 |
| EXPECTANCY | FAIL | -0.162 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.021 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.400 | PBO 0.400 within limit |
| DEFLATED_SHARPE | FAIL | 0.011 | DSR 0.0109 below minimum |
| COST_STRESS_2X | FAIL | -0.106 | doubling costs wipes out the edge |

### M05 CarryMomentumAgreement — USDJPY

Mechanism: Carry performs worst exactly when momentum turns against it; requiring 60-day momentum to agree filters out the unwind phase while keeping the yield flow of aligned periods.

Falsifiable claim: The agreement-gated carry position beats ungated M04 by at least 0.2 Sharpe net of costs and keeps a net expectancy above 0, while being invested on at least 30% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 71 < 100 |
| EXPECTANCY | PASS | 0.478 | positive expectancy |
| WALK_FORWARD | PASS | 0.800 | 0.800 of folds profitable |
| HOLDOUT | PASS | 0.032 | holdout profitable |
| CSCV_PBO | FAIL | 0.900 | PBO 0.900 above limit |
| DEFLATED_SHARPE | FAIL | 0.483 | DSR 0.4829 below minimum |
| COST_STRESS_2X | PASS | 0.339 | profitable at 2x costs |

### M05 CarryMomentumAgreement — EURJPY

Mechanism: Carry performs worst exactly when momentum turns against it; requiring 60-day momentum to agree filters out the unwind phase while keeping the yield flow of aligned periods.

Falsifiable claim: The agreement-gated carry position beats ungated M04 by at least 0.2 Sharpe net of costs and keeps a net expectancy above 0, while being invested on at least 30% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 2947.000 | trades 98 < 100 |
| EXPECTANCY | PASS | 0.126 | positive expectancy |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | PASS | 0.024 | holdout profitable |
| CSCV_PBO | PASS | 0.200 | PBO 0.200 within limit |
| DEFLATED_SHARPE | FAIL | 0.105 | DSR 0.1053 below minimum |
| COST_STRESS_2X | PASS | 0.086 | profitable at 2x costs |

### M06 VolatilityManagedMomentum — EURUSD

Mechanism: Momentum crashes cluster in volatility spikes, when leveraged trend followers are forced to cut into falling liquidity; standing aside when realised volatility is far above its norm avoids paying that forced flow.

Falsifiable claim: 60-day momentum with the volatility gate improves on ungated M01 by at least 0.15 Sharpe net of costs, with a maximum drawdown at least 20% smaller, and is flat on no more than 40% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 2947.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.027 | positive expectancy |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | PASS | 0.011 | holdout profitable |
| CSCV_PBO | FAIL | 0.529 | PBO 0.529 above limit |
| DEFLATED_SHARPE | FAIL | 0.055 | DSR 0.0554 below minimum |
| COST_STRESS_2X | FAIL | -0.008 | doubling costs wipes out the edge |

### M06 VolatilityManagedMomentum — GBPUSD

Mechanism: Momentum crashes cluster in volatility spikes, when leveraged trend followers are forced to cut into falling liquidity; standing aside when realised volatility is far above its norm avoids paying that forced flow.

Falsifiable claim: 60-day momentum with the volatility gate improves on ungated M01 by at least 0.15 Sharpe net of costs, with a maximum drawdown at least 20% smaller, and is flat on no more than 40% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 2947.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.196 | positive expectancy |
| WALK_FORWARD | PASS | 0.800 | 0.800 of folds profitable |
| HOLDOUT | FAIL | -0.059 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.629 | PBO 0.629 above limit |
| DEFLATED_SHARPE | FAIL | 0.155 | DSR 0.1552 below minimum |
| COST_STRESS_2X | PASS | 0.143 | profitable at 2x costs |

### M06 VolatilityManagedMomentum — USDJPY

Mechanism: Momentum crashes cluster in volatility spikes, when leveraged trend followers are forced to cut into falling liquidity; standing aside when realised volatility is far above its norm avoids paying that forced flow.

Falsifiable claim: 60-day momentum with the volatility gate improves on ungated M01 by at least 0.15 Sharpe net of costs, with a maximum drawdown at least 20% smaller, and is flat on no more than 40% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 2947.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.436 | positive expectancy |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | FAIL | -0.008 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.400 | PBO 0.400 within limit |
| DEFLATED_SHARPE | FAIL | 0.425 | DSR 0.4254 below minimum |
| COST_STRESS_2X | PASS | 0.374 | profitable at 2x costs |

### M06 VolatilityManagedMomentum — EURJPY

Mechanism: Momentum crashes cluster in volatility spikes, when leveraged trend followers are forced to cut into falling liquidity; standing aside when realised volatility is far above its norm avoids paying that forced flow.

Falsifiable claim: 60-day momentum with the volatility gate improves on ungated M01 by at least 0.15 Sharpe net of costs, with a maximum drawdown at least 20% smaller, and is flat on no more than 40% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 2947.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 2947.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.040 | positive expectancy |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.016 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.743 | PBO 0.743 above limit |
| DEFLATED_SHARPE | FAIL | 0.061 | DSR 0.0609 below minimum |
| COST_STRESS_2X | PASS | 0.011 | profitable at 2x costs |

## Not evaluated

The session/volatility family (S01–S08) requires hourly bars. Dukascopy and Stooq were unreachable from the build sandbox and no other free intraday FX source was available, so these hypotheses were NOT EVALUATED in this sprint. Not evaluated is not passed: they remain untested until intraday data is obtained.

- **S01 AsiaRangeLondonBreakout** (1h) — not evaluated: no hourly data.
- **S02 AsiaRangeLondonFade** (1h) — not evaluated: no hourly data.
- **S03 LondonOpenMomentum** (1h) — not evaluated: no hourly data.
- **S04 LondonMoveNewYorkReversal** (1h) — not evaluated: no hourly data.
- **S05 VolatilitySpikePullbackFade** (1h) — not evaluated: no hourly data.
- **S06 AsiaCompressionBreakout** (1h) — not evaluated: no hourly data.
- **S07 SessionDriftLondonMorning** (1h) — not evaluated: no hourly data.
- **S08 WeekendGapFade** (1h) — not evaluated: no hourly data.

## Conclusions

**No hypothesis passed the fortress in this sprint.** That is a valid and valuable outcome: the daily carry/momentum family, on FRED reference data with realistic costs and full multiple-testing correction, produced no survivor. Per the roadmap, the correct response is not to loosen the fortress or re-tune these IDs — it is to test a different family/data (the session family on real intraday data, or INR-specific structure) with new hypothesis IDs.

Every rejected hypothesis stays rejected under its ID. Any follow-up idea gets a new ID and increases the multiple-testing trial count.

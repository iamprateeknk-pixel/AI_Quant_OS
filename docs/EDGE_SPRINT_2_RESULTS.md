# Edge Sprint 2 — Results (Session/Volatility Family)

**Date:** 2026-10-21  
**Scope:** the pre-registered session/volatility hypotheses S01–S08 from `src/ai_quant_os/edge_hypotheses.py` (not evaluated in Sprint 1 for lack of hourly data), evaluated once through the Validation Fortress (`src/ai_quant_os/validation_fortress.py`, default thresholds, unchanged). No parameter was tuned after seeing results; a FAIL is a rejection under the hypothesis's own falsification criterion. Sprint 1's M01–M06 were NOT re-run and stay rejected.

## Data acquisition (Phase A) — routes attempted

1. **Dukascopy aggregated candle files** (`BID_candles_hour_1.bi5`, urllib and `requests`, several naming variants): HTTP 404 for every variant — the candle tree does not serve these files at the documented paths. **Dukascopy tick files** (`{HH}h_ticks.bi5`) DO download via `requests` (curl receives an empty body; urllib connections are dropped by the sandbox egress proxy) and decode as valid LZMA tick streams, but per-hour fetching through the proxy was too slow/flaky to assemble multi-year series (a serial 24-file single-day fetch exceeded 280 s; a parallel throughput run died without output). Route abandoned on throughput, not on data quality.
2. **GitHub public datasets** (connector search): most candidates were stubs or samples (e.g. 3-byte placeholder CSVs in `mmayes313/jordan-rl-trading-bot`; a README-only repo at `FX-Data/FX-Data-EURUSD-DS`; ~5–12 KB samples elsewhere). One candidate passed screening: `millionairealde-creator/advanced_forex_bot`, `data/{EUR_USD,GBP_USD,USD_JPY,EUR_JPY}_H1.csv` at commit `45668048ad0dbb351242261c4bc67486ba66606c`, fetched from commit-pinned raw URLs and accepted only on exact SHA-256 match with the bytes validated in Phase A. **This is the dataset evaluated below.**
3. **HistData.com**: not reached — route 2 produced a fully validated dataset for all four pairs, so the form-based flow was not needed.

## Dataset

Hourly candles (timestamp, open, high, low, close, volume, complete), timestamps in UTC. Bars are treated as mid-price candles; spread is charged through the Sprint 1 cost model. The exporter's `complete` flag is applied uniformly: only `complete=True` bars are used. Exactly 500 tail bars per file are flagged incomplete; in USDJPY that block is a wrong-instrument splice (prices ≈ 1.1), which is precisely what the flag exists to exclude. EURJPY is a genuine traded cross in this export, not a derived product.

| Instrument | Bars used | Dropped (incomplete) | First | Last | SHA-256 |
|---|---:|---:|---|---|---|
| EURUSD | 30857 | 500 | 2020-11-10T21:00 | 2025-11-14T20:00 | `79be0bf5ed512548…` |
| GBPUSD | 30737 | 500 | 2020-11-10T21:00 | 2025-10-19T23:00 | `2bfe905149ce258f…` |
| USDJPY | 25768 | 500 | 2020-11-10T21:00 | 2024-12-31T21:00 | `c685e01801836a91…` |
| EURJPY | 31127 | 0 | 2020-11-16T04:00 | 2025-11-14T21:00 | `a9fb51f6cfcd68a2…` |

Validation on the kept bars (all four pairs):

| Instrument | Non-monotonic | OHLC violations | Out-of-range closes | Gaps > 72h | FRED daily-ret corr | FRED weekly-ret corr |
|---|---:|---:|---:|---:|---:|---:|
| EURUSD | 0 | 0 | 0 | 6 | 0.856 | 0.968 |
| GBPUSD | 0 | 0 | 0 | 5 | 0.859 | 0.967 |
| USDJPY | 0 | 0 | 0 | 4 | 0.883 | 0.977 |
| EURJPY | 0 | 0 | 0 | 5 | 0.885 | 0.974 |

The FRED comparison uses each pair's last hourly close per UTC day against the FRED noon (12:00 ET) buying rate, so a sampling offset of ~7.5 h is built into the daily figure (expected ceiling ≈ 0.9); the weekly figure (0.97) is the cleaner authenticity signal. Intraday extremes are reproduced at the correct UTC hours: GBPUSD 2022-09-26 low 1.03595 at 01:00 UTC (flash crash), EURUSD 2022-07-14 low 0.99522 at 13:00 UTC (parity break), USDJPY 2022-10-21 high 151.946 at 12:00 UTC and low 146.197 at 16:00 UTC (intervention), USDJPY 2024-08-05 low 141.688 (carry unwind). Coverage differs by pair because the export's complete data ends earlier for GBPUSD (2025-10-19) and USDJPY (2024-12-31); every pair exceeds the 2-year minimum. Provenance risk is stated plainly: this is a single-uploader third-party export of unknown broker origin that passed every structural and cross-source check applied; it is not an official Dukascopy/broker feed.

## Cost model (modelled, not observed) — Sprint 1, unchanged

One-way cost per 1.0 unit of position change (spread + slippage allowance), charged in the first period the new position is held. Fees are zero. The fortress re-tests everything at **2x** costs (COST_STRESS_2X gate).

| Instrument | One-way cost | In pips |
|---|---:|---:|
| EURUSD | 8e-05 | 0.8 |
| GBPUSD | 0.0001 | 1.0 |
| USDJPY | 0.008 | 0.8 |
| EURJPY | 0.01 | 1.0 |

## Fortress gates (defaults, unchanged)

DATA_INTEGRITY · SAMPLE_SIZE (>= 100 trades, >= 240 periods) · EXPECTANCY · WALK_FORWARD (5 folds, >= 60% positive) · HOLDOUT (final 25%) · CSCV_PBO (<= 0.50) · DEFLATED_SHARPE (>= 0.95) · COST_STRESS_2X. Verdict PASS requires every gate to pass.

**Multiple-testing accounting:** the deflated Sharpe correction is charged for all trials in the programme: Sprint 1's 24 base evaluations + Sprint 2's 32 = **56 trials**, and the trial-Sharpe population pools both sprints' base runs. Sprint 1 trial Sharpes were reconstructed exactly by re-running its base simulations in-process on FRED data.

## Headline result

**0 of 32 hypothesis/instrument evaluations passed the fortress.**

## Results by evaluation

| Hypothesis | Instrument | Trades | Net return* | Ann. Sharpe | PBO | DSR | Stressed net* | Verdict | Failed gates |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| S01 AsiaRangeLondonBreakout | EURUSD | 1205 | -0.81% | -0.03 | 0.51 | 0.00 | -18.27% | FAIL | EXPECTANCY, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S01 AsiaRangeLondonBreakout | GBPUSD | 1201 | -26.30% | -0.79 | 0.24 | 0.00 | -44.97% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S01 AsiaRangeLondonBreakout | USDJPY | 866 | -2.15% | -0.09 | 0.94 | 0.00 | -12.83% | FAIL | EXPECTANCY, WALK_FORWARD, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S01 AsiaRangeLondonBreakout | EURJPY | 1098 | +3.51% | 0.12 | 0.96 | 0.00 | -11.44% | FAIL | WALK_FORWARD, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S02 AsiaRangeLondonFade | EURUSD | 1205 | -24.31% | -0.92 | 0.51 | 0.00 | -41.78% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S02 AsiaRangeLondonFade | GBPUSD | 1201 | -12.57% | -0.43 | 0.96 | 0.00 | -31.25% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S02 AsiaRangeLondonFade | USDJPY | 866 | -24.87% | -1.14 | 0.47 | 0.00 | -35.55% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S02 AsiaRangeLondonFade | EURJPY | 1098 | -28.56% | -1.08 | 0.54 | 0.00 | -43.52% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S03 LondonOpenMomentum | EURUSD | 1284 | +5.82% | 0.20 | 0.64 | 0.00 | -12.80% | FAIL | CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S03 LondonOpenMomentum | GBPUSD | 1272 | -27.98% | -0.85 | 0.93 | 0.00 | -47.75% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S03 LondonOpenMomentum | USDJPY | 1067 | +9.14% | 0.34 | 0.83 | 0.00 | -3.99% | FAIL | WALK_FORWARD, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S03 LondonOpenMomentum | EURJPY | 1290 | +3.85% | 0.13 | 0.77 | 0.00 | -13.64% | FAIL | HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S04 LondonMoveNewYorkReversal | EURUSD | 815 | -12.45% | -0.67 | 0.83 | 0.00 | -24.27% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S04 LondonMoveNewYorkReversal | GBPUSD | 801 | -8.94% | -0.44 | 1.00 | 0.00 | -21.40% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S04 LondonMoveNewYorkReversal | USDJPY | 555 | -1.93% | -0.12 | 0.67 | 0.00 | -8.79% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S04 LondonMoveNewYorkReversal | EURJPY | 720 | -5.58% | -0.30 | 0.03 | 0.00 | -15.34% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S05 VolatilitySpikePullbackFade | EURUSD | 1733 | -32.36% | -1.72 | 0.00 | 0.00 | -57.53% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S05 VolatilitySpikePullbackFade | GBPUSD | 1686 | -46.62% | -2.22 | 0.01 | 0.00 | -72.85% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S05 VolatilitySpikePullbackFade | USDJPY | 1223 | -34.89% | -2.10 | 0.34 | 0.00 | -49.96% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S05 VolatilitySpikePullbackFade | EURJPY | 1465 | -20.60% | -1.02 | 0.10 | 0.00 | -40.52% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S06 AsiaCompressionBreakout | EURUSD | 411 | +1.43% | 0.08 | 0.71 | 0.00 | -4.52% | FAIL | WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S06 AsiaCompressionBreakout | GBPUSD | 420 | -6.39% | -0.32 | 0.34 | 0.00 | -12.92% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S06 AsiaCompressionBreakout | USDJPY | 334 | +0.79% | 0.05 | 0.51 | 0.00 | -3.28% | FAIL | CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S06 AsiaCompressionBreakout | EURJPY | 414 | +13.05% | 0.74 | 0.70 | 0.00 | +7.48% | FAIL | CSCV_PBO, DEFLATED_SHARPE |
| S07 SessionDriftLondonMorning | EURUSD | 1286 | -18.14% | -1.23 | 0.26 | 0.00 | -36.79% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S07 SessionDriftLondonMorning | GBPUSD | 1281 | -20.05% | -1.15 | 0.23 | 0.00 | -39.96% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S07 SessionDriftLondonMorning | USDJPY | 1074 | -7.24% | -0.54 | 0.20 | 0.00 | -20.46% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S07 SessionDriftLondonMorning | EURJPY | 1297 | -11.92% | -0.64 | 0.37 | 0.00 | -29.50% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S08 WeekendGapFade | EURUSD | 0 | +0.00% | 0.00 | 1.00 | 0.00 | +0.00% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S08 WeekendGapFade | GBPUSD | 0 | +0.00% | 0.00 | 1.00 | 0.00 | +0.00% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S08 WeekendGapFade | USDJPY | 0 | +0.00% | 0.00 | 1.00 | 0.00 | +0.00% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S08 WeekendGapFade | EURJPY | 0 | +0.00% | 0.00 | 1.00 | 0.00 | +0.00% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |

\* Net return is the sum of per-period net returns (not compounded), the same quantity the fortress gates use.

## Gate detail

### S01 AsiaRangeLondonBreakout — EURUSD

Mechanism: Asia trades thin, range-bound flow; at the London open, institutional orders and stop clusters beyond the Asia range force one-sided hedging by dealers and late momentum entrants on the other side.

Falsifiable claim: On breakout days the stance earns a mean return of at least +0.15 x the Asia range width per trade net of costs, with a win rate of at least 45% across the four pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.027 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | PASS | 0.007 | holdout profitable |
| CSCV_PBO | FAIL | 0.514 | PBO 0.514 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.183 | doubling costs wipes out the edge |

### S01 AsiaRangeLondonBreakout — GBPUSD

Mechanism: Asia trades thin, range-bound flow; at the London open, institutional orders and stop clusters beyond the Asia range force one-sided hedging by dealers and late momentum entrants on the other side.

Falsifiable claim: On breakout days the stance earns a mean return of at least +0.15 x the Asia range width per trade net of costs, with a win rate of at least 45% across the four pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.794 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.009 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.243 | PBO 0.243 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.450 | doubling costs wipes out the edge |

### S01 AsiaRangeLondonBreakout — USDJPY

Mechanism: Asia trades thin, range-bound flow; at the London open, institutional orders and stop clusters beyond the Asia range force one-sided hedging by dealers and late momentum entrants on the other side.

Falsifiable claim: On breakout days the stance earns a mean return of at least +0.15 x the Asia range width per trade net of costs, with a win rate of at least 45% across the four pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.089 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | PASS | 0.027 | holdout profitable |
| CSCV_PBO | FAIL | 0.943 | PBO 0.943 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.128 | doubling costs wipes out the edge |

### S01 AsiaRangeLondonBreakout — EURJPY

Mechanism: Asia trades thin, range-bound flow; at the London open, institutional orders and stop clusters beyond the Asia range force one-sided hedging by dealers and late momentum entrants on the other side.

Falsifiable claim: On breakout days the stance earns a mean return of at least +0.15 x the Asia range width per trade net of costs, with a win rate of at least 45% across the four pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.121 | positive expectancy |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | PASS | 0.002 | holdout profitable |
| CSCV_PBO | FAIL | 0.957 | PBO 0.957 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.114 | doubling costs wipes out the edge |

### S02 AsiaRangeLondonFade — EURUSD

Mechanism: Early London breaks often sweep stop orders placed just beyond the Asia range; once the stops are filled there is no follow-through flow, and liquidity providers who absorbed the sweep sell the move back.

Falsifiable claim: Fading the first break earns at least +0.10 x the Asia range width per trade net of costs, reaching the range midpoint on at least 50% of breakout days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.925 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.020 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.514 | PBO 0.514 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.418 | doubling costs wipes out the edge |

### S02 AsiaRangeLondonFade — GBPUSD

Mechanism: Early London breaks often sweep stop orders placed just beyond the Asia range; once the stops are filled there is no follow-through flow, and liquidity providers who absorbed the sweep sell the move back.

Falsifiable claim: Fading the first break earns at least +0.10 x the Asia range width per trade net of costs, reaching the range midpoint on at least 50% of breakout days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.433 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.010 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.957 | PBO 0.957 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.312 | doubling costs wipes out the edge |

### S02 AsiaRangeLondonFade — USDJPY

Mechanism: Early London breaks often sweep stop orders placed just beyond the Asia range; once the stops are filled there is no follow-through flow, and liquidity providers who absorbed the sweep sell the move back.

Falsifiable claim: Fading the first break earns at least +0.10 x the Asia range width per trade net of costs, reaching the range midpoint on at least 50% of breakout days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | FAIL | -1.144 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.033 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.471 | PBO 0.471 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.356 | doubling costs wipes out the edge |

### S02 AsiaRangeLondonFade — EURJPY

Mechanism: Early London breaks often sweep stop orders placed just beyond the Asia range; once the stops are filled there is no follow-through flow, and liquidity providers who absorbed the sweep sell the move back.

Falsifiable claim: Fading the first break earns at least +0.10 x the Asia range width per trade net of costs, reaching the range midpoint on at least 50% of breakout days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | FAIL | -1.083 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.010 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.543 | PBO 0.543 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.435 | doubling costs wipes out the edge |

### S03 LondonOpenMomentum — EURUSD

Mechanism: The first London hour aggregates overnight information and large institutional execution programmes; under-reaction and benchmark order flow keep pushing price the same way through the morning.

Falsifiable claim: The 08:00-16:00 stance in the direction of the 07:00 hour earns a mean of at least +2 bp per day net of costs, positive on at least 52% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.197 | positive expectancy |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | PASS | 0.009 | holdout profitable |
| CSCV_PBO | FAIL | 0.643 | PBO 0.643 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.128 | doubling costs wipes out the edge |

### S03 LondonOpenMomentum — GBPUSD

Mechanism: The first London hour aggregates overnight information and large institutional execution programmes; under-reaction and benchmark order flow keep pushing price the same way through the morning.

Falsifiable claim: The 08:00-16:00 stance in the direction of the 07:00 hour earns a mean of at least +2 bp per day net of costs, positive on at least 52% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.847 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.002 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.929 | PBO 0.929 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.477 | doubling costs wipes out the edge |

### S03 LondonOpenMomentum — USDJPY

Mechanism: The first London hour aggregates overnight information and large institutional execution programmes; under-reaction and benchmark order flow keep pushing price the same way through the morning.

Falsifiable claim: The 08:00-16:00 stance in the direction of the 07:00 hour earns a mean of at least +2 bp per day net of costs, positive on at least 52% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.339 | positive expectancy |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | PASS | 0.019 | holdout profitable |
| CSCV_PBO | FAIL | 0.829 | PBO 0.829 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.040 | doubling costs wipes out the edge |

### S03 LondonOpenMomentum — EURJPY

Mechanism: The first London hour aggregates overnight information and large institutional execution programmes; under-reaction and benchmark order flow keep pushing price the same way through the morning.

Falsifiable claim: The 08:00-16:00 stance in the direction of the 07:00 hour earns a mean of at least +2 bp per day net of costs, positive on at least 52% of days.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.129 | positive expectancy |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | FAIL | -0.014 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.771 | PBO 0.771 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.136 | doubling costs wipes out the edge |

### S04 LondonMoveNewYorkReversal — EURUSD

Mechanism: An outsized London morning move is often positioning-driven rather than information-driven; New York entrants and profit-taking London desks on the other side fade the overextension in the afternoon.

Falsifiable claim: When the morning move exceeds 1.0 x ATR(14), the 13:00-20:00 fade earns a mean of at least +0.25 x ATR(14) per signal day net of costs, on at least 30 signal days per pair.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.666 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.010 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.829 | PBO 0.829 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.243 | doubling costs wipes out the edge |

### S04 LondonMoveNewYorkReversal — GBPUSD

Mechanism: An outsized London morning move is often positioning-driven rather than information-driven; New York entrants and profit-taking London desks on the other side fade the overextension in the afternoon.

Falsifiable claim: When the morning move exceeds 1.0 x ATR(14), the 13:00-20:00 fade earns a mean of at least +0.25 x ATR(14) per signal day net of costs, on at least 30 signal days per pair.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.444 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.007 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 1.000 | PBO 1.000 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.214 | doubling costs wipes out the edge |

### S04 LondonMoveNewYorkReversal — USDJPY

Mechanism: An outsized London morning move is often positioning-driven rather than information-driven; New York entrants and profit-taking London desks on the other side fade the overextension in the afternoon.

Falsifiable claim: When the morning move exceeds 1.0 x ATR(14), the 13:00-20:00 fade earns a mean of at least +0.25 x ATR(14) per signal day net of costs, on at least 30 signal days per pair.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.124 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.007 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.671 | PBO 0.671 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.088 | doubling costs wipes out the edge |

### S04 LondonMoveNewYorkReversal — EURJPY

Mechanism: An outsized London morning move is often positioning-driven rather than information-driven; New York entrants and profit-taking London desks on the other side fade the overextension in the afternoon.

Falsifiable claim: When the morning move exceeds 1.0 x ATR(14), the 13:00-20:00 fade earns a mean of at least +0.25 x ATR(14) per signal day net of costs, on at least 30 signal days per pair.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.303 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.003 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.029 | PBO 0.029 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.153 | doubling costs wipes out the edge |

### S05 VolatilitySpikePullbackFade — EURUSD

Mechanism: A single bar with more than double the normal range closing at its extreme is typically a stop cascade or a fat-finger liquidity vacuum; market makers who filled the spike re-price back toward fair value.

Falsifiable claim: The fade held for 3 bars earns a mean of at least +0.20 x ATR(20) per event net of costs, across at least 100 events per pair.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | FAIL | -1.715 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.030 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.000 | PBO 0.000 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.575 | doubling costs wipes out the edge |

### S05 VolatilitySpikePullbackFade — GBPUSD

Mechanism: A single bar with more than double the normal range closing at its extreme is typically a stop cascade or a fat-finger liquidity vacuum; market makers who filled the spike re-price back toward fair value.

Falsifiable claim: The fade held for 3 bars earns a mean of at least +0.20 x ATR(20) per event net of costs, across at least 100 events per pair.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -2.224 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.032 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.014 | PBO 0.014 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.729 | doubling costs wipes out the edge |

### S05 VolatilitySpikePullbackFade — USDJPY

Mechanism: A single bar with more than double the normal range closing at its extreme is typically a stop cascade or a fat-finger liquidity vacuum; market makers who filled the spike re-price back toward fair value.

Falsifiable claim: The fade held for 3 bars earns a mean of at least +0.20 x ATR(20) per event net of costs, across at least 100 events per pair.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | FAIL | -2.102 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.034 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.343 | PBO 0.343 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.500 | doubling costs wipes out the edge |

### S05 VolatilitySpikePullbackFade — EURJPY

Mechanism: A single bar with more than double the normal range closing at its extreme is typically a stop cascade or a fat-finger liquidity vacuum; market makers who filled the spike re-price back toward fair value.

Falsifiable claim: The fade held for 3 bars earns a mean of at least +0.20 x ATR(20) per event net of costs, across at least 100 events per pair.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | FAIL | -1.021 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.024 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.100 | PBO 0.100 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.405 | doubling costs wipes out the edge |

### S06 AsiaCompressionBreakout — EURUSD

Mechanism: An unusually compressed Asia range means stops and breakout orders are packed tightly on both sides; London flow needs little size to trigger a cascade, so compressed days break more cleanly (S01 effect, conditioned on compression).

Falsifiable claim: On days in the bottom 30% of trailing Asia widths, the breakout stance earns at least +0.20 x the Asia range width per trade net of costs, beating the unconditional S01 expectancy by at least 25%.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.079 | positive expectancy |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.006 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.714 | PBO 0.714 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.045 | doubling costs wipes out the edge |

### S06 AsiaCompressionBreakout — GBPUSD

Mechanism: An unusually compressed Asia range means stops and breakout orders are packed tightly on both sides; London flow needs little size to trigger a cascade, so compressed days break more cleanly (S01 effect, conditioned on compression).

Falsifiable claim: On days in the bottom 30% of trailing Asia widths, the breakout stance earns at least +0.20 x the Asia range width per trade net of costs, beating the unconditional S01 expectancy by at least 25%.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.317 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.016 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.343 | PBO 0.343 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.129 | doubling costs wipes out the edge |

### S06 AsiaCompressionBreakout — USDJPY

Mechanism: An unusually compressed Asia range means stops and breakout orders are packed tightly on both sides; London flow needs little size to trigger a cascade, so compressed days break more cleanly (S01 effect, conditioned on compression).

Falsifiable claim: On days in the bottom 30% of trailing Asia widths, the breakout stance earns at least +0.20 x the Asia range width per trade net of costs, beating the unconditional S01 expectancy by at least 25%.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.050 | positive expectancy |
| WALK_FORWARD | PASS | 0.800 | 0.800 of folds profitable |
| HOLDOUT | PASS | 0.020 | holdout profitable |
| CSCV_PBO | FAIL | 0.514 | PBO 0.514 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.033 | doubling costs wipes out the edge |

### S06 AsiaCompressionBreakout — EURJPY

Mechanism: An unusually compressed Asia range means stops and breakout orders are packed tightly on both sides; London flow needs little size to trigger a cascade, so compressed days break more cleanly (S01 effect, conditioned on compression).

Falsifiable claim: On days in the bottom 30% of trailing Asia widths, the breakout stance earns at least +0.20 x the Asia range width per trade net of costs, beating the unconditional S01 expectancy by at least 25%.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.739 | positive expectancy |
| WALK_FORWARD | PASS | 0.800 | 0.800 of folds profitable |
| HOLDOUT | PASS | 0.003 | holdout profitable |
| CSCV_PBO | FAIL | 0.700 | PBO 0.700 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0002 below minimum |
| COST_STRESS_2X | PASS | 0.075 | profitable at 2x costs |

### S07 SessionDriftLondonMorning — EURUSD

Mechanism: Structural morning flows (European corporate hedging, fund benchmark execution and dealer inventory recycling) create a persistent time-of-day drift in the London morning, identical across pairs.

Falsifiable claim: Being long 07:00-10:00 UTC every weekday earns a mean of at least +0.5 bp per hour held net of costs, with a positive mean in at least 3 of the 4 pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | FAIL | -1.229 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.011 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.257 | PBO 0.257 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.368 | doubling costs wipes out the edge |

### S07 SessionDriftLondonMorning — GBPUSD

Mechanism: Structural morning flows (European corporate hedging, fund benchmark execution and dealer inventory recycling) create a persistent time-of-day drift in the London morning, identical across pairs.

Falsifiable claim: Being long 07:00-10:00 UTC every weekday earns a mean of at least +0.5 bp per hour held net of costs, with a positive mean in at least 3 of the 4 pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -1.146 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.014 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.229 | PBO 0.229 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.400 | doubling costs wipes out the edge |

### S07 SessionDriftLondonMorning — USDJPY

Mechanism: Structural morning flows (European corporate hedging, fund benchmark execution and dealer inventory recycling) create a persistent time-of-day drift in the London morning, identical across pairs.

Falsifiable claim: Being long 07:00-10:00 UTC every weekday earns a mean of at least +0.5 bp per hour held net of costs, with a positive mean in at least 3 of the 4 pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.544 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.006 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.200 | PBO 0.200 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.205 | doubling costs wipes out the edge |

### S07 SessionDriftLondonMorning — EURJPY

Mechanism: Structural morning flows (European corporate hedging, fund benchmark execution and dealer inventory recycling) create a persistent time-of-day drift in the London morning, identical across pairs.

Falsifiable claim: Being long 07:00-10:00 UTC every weekday earns a mean of at least +0.5 bp per hour held net of costs, with a positive mean in at least 3 of the 4 pairs.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.644 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.002 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.371 | PBO 0.371 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.295 | doubling costs wipes out the edge |

### S08 WeekendGapFade — EURUSD

Mechanism: Weekend gaps are usually headline-driven overreaction priced in thin Sunday/Monday-open liquidity; dealers who quoted the gap wide expect retracement once normal London liquidity returns.

Falsifiable claim: Fading gaps larger than 1.0 x ATR(14) until Monday 12:00 UTC earns a mean of at least +0.25 x the gap size per event net of costs, across at least 40 gap events in total.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 30857.000 | trades 0 < 100 |
| EXPECTANCY | FAIL | 0.000 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | 0.000 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 1.000 | PBO 1.000 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | 0.000 | doubling costs wipes out the edge |

### S08 WeekendGapFade — GBPUSD

Mechanism: Weekend gaps are usually headline-driven overreaction priced in thin Sunday/Monday-open liquidity; dealers who quoted the gap wide expect retracement once normal London liquidity returns.

Falsifiable claim: Fading gaps larger than 1.0 x ATR(14) until Monday 12:00 UTC earns a mean of at least +0.25 x the gap size per event net of costs, across at least 40 gap events in total.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 30737.000 | trades 0 < 100 |
| EXPECTANCY | FAIL | 0.000 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | 0.000 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 1.000 | PBO 1.000 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | 0.000 | doubling costs wipes out the edge |

### S08 WeekendGapFade — USDJPY

Mechanism: Weekend gaps are usually headline-driven overreaction priced in thin Sunday/Monday-open liquidity; dealers who quoted the gap wide expect retracement once normal London liquidity returns.

Falsifiable claim: Fading gaps larger than 1.0 x ATR(14) until Monday 12:00 UTC earns a mean of at least +0.25 x the gap size per event net of costs, across at least 40 gap events in total.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 25768.000 | trades 0 < 100 |
| EXPECTANCY | FAIL | 0.000 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | 0.000 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 1.000 | PBO 1.000 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | 0.000 | doubling costs wipes out the edge |

### S08 WeekendGapFade — EURJPY

Mechanism: Weekend gaps are usually headline-driven overreaction priced in thin Sunday/Monday-open liquidity; dealers who quoted the gap wide expect retracement once normal London liquidity returns.

Falsifiable claim: Fading gaps larger than 1.0 x ATR(14) until Monday 12:00 UTC earns a mean of at least +0.25 x the gap size per event net of costs, across at least 40 gap events in total.

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 31127.000 | trades 0 < 100 |
| EXPECTANCY | FAIL | 0.000 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | 0.000 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 1.000 | PBO 1.000 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | 0.000 | doubling costs wipes out the edge |

## Conclusions

**No session/volatility hypothesis passed the fortress in this sprint.** The family that Sprint 1 could not test has now been tested on five years of validated hourly data, with the full programme-wide multiple-testing correction, and produced no survivor. Per the roadmap, the correct response is not to loosen the fortress or re-tune these IDs — rejected IDs stay rejected.

Every rejected hypothesis stays rejected under its ID. Any follow-up idea gets a new ID and increases the multiple-testing trial count. INR pairs remain **PENDING_DATA**.

---

## Coordinator's addendum — S08 zero-trade diagnosis (post-run, verdict unchanged)

S08 produced **zero trades on all four pairs**, which was diagnosed after the run rather than accepted at face value. Root cause is a week-boundary convention mismatch, not an absence of weekend gaps:

- The pre-registered `_s08_signal` only inspects **Monday** day-groups whose immediately preceding bar belongs to **Friday**.
- This dataset's trading week opens **Sunday 22:00 UTC** (OANDA-style): every Monday group is preceded by Sunday-evening bars, so the trigger condition can never fire.
- The gaps themselves exist in the data: EURUSD alone has 261 weekly opens, 36 with gaps > 15 pips, 22 > 25 pips, largest 115.2 pips — but they sit on the Sunday-open bar, which the specification does not look at.

The FAIL verdict stands: under its registered specification S08 generated no sample, and SAMPLE_SIZE correctly failed it. A Sunday-open variant would be a **new hypothesis ID** (with the trial count increased accordingly), not a re-run of S08. Note for future hourly work: Dukascopy also opens the week on Sunday evening UTC, so the same mismatch would be expected there — the S08 specification implicitly assumes a Monday-open feed convention.

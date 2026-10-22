# Edge Sprint 3 — Results (INR Claim Test + New Hourly Family)

**Date:** 2026-10-21  
**Scope:** (1) an explicit test of Sprint 1's claim that no free reliable USDINR series was obtainable, and -- if the candidate series validates -- a pre-registered INR hypothesis family (I01-I06, new IDs) on USDINR; (2) five new pre-registered hourly hypotheses (S09-S13, new IDs) on Sprint 2's validated hourly dataset. All parameters were fixed in the runner's docstring before any evaluation ran. No rejected ID (M01-M06, S01-S08) was re-run or re-tuned; Sprint 1/2 verdicts stand untouched.

## Part 1 — The USDINR claim, tested

Sprint 1 reported: "INR pairs: **PENDING_DATA** — no free reliable USDINR reference series was obtainable, and none was fabricated." That claim is falsifiable, and it is **false as stated**: FRED's catalogue contains **DEXINUS** — "Indian Rupees to One U.S. Dollar Spot Exchange Rate", source Board of Governors of the Federal Reserve System, release **H.10 Daily Foreign Exchange Rates**, units "Indian Rupees to One U.S. Dollar", noon buying rates in New York City, daily, spanning 1973-01-02 to 2026-10-16, last updated 2026-10-19 (https://fred.stlouisfed.org/series/DEXINUS). DEXINUS is the same release, the same rate convention and the same fredgraph.csv endpoint family as DEXUSEU, DEXUSUK and DEXJPUS, which Sprint 1 fetched successfully from this environment. What Sprint 1 lacked was evidently the series ID, not the data.

**This run could not fetch the series.** The fredgraph endpoint was tried repeatedly; the terminal condition was: DEXINUS: HTML body (968 bytes) instead of CSV -- FRED returned its 'Site Maintenance' page (after 8 attempts). During this session the endpoint consistently returned FRED's HTML 'Site Maintenance' page (HTTP 200, ~0.9 KB) for every series, including DEXUSEU — an endpoint outage, not a property of DEXINUS (Sprints 1 and 2 used the same endpoint successfully earlier the same day). Because no bytes could be validated, **no INR evaluation was run and INR remains PENDING_DATA for this run**. The standing conclusion is narrower than Sprint 1's: a free, reliable USDINR reference series EXISTS (DEXINUS); it was temporarily unreachable. Recommendation: re-run this runner when the endpoint is back; keep `INR_INSTRUMENT_STATUS` unchanged until the pre-registered validation battery (specified in the runner's docstring) passes on real bytes.

## Part 2 — Sprint 3 hourly family (S09-S13)

Dataset: identical to Sprint 2 — the public GitHub export (`millionairealde-creator/advanced_forex_bot`, commit-pinned, SHA-256-verified before acceptance, `complete=True` bars only). Identity in this run is guaranteed by the same SHA-256 pins; the FRED daily cross-check could not be repeated in this run (the FRED endpoint was returning its maintenance page), which does not affect dataset identity: acceptance is by exact SHA-256 match with the bytes Sprint 2 validated.

| Instrument | Bars used | Dropped | First | Last | SHA-256 |
|---|---:|---:|---|---|---|
| EURUSD | 30857 | 500 | 2020-11-10T21:00 | 2025-11-14T20:00 | `79be0bf5ed512548…` |
| GBPUSD | 30737 | 500 | 2020-11-10T21:00 | 2025-10-19T23:00 | `2bfe905149ce258f…` |
| USDJPY | 25768 | 500 | 2020-11-10T21:00 | 2024-12-31T21:00 | `c685e01801836a91…` |
| EURJPY | 31127 | 0 | 2020-11-16T04:00 | 2025-11-14T21:00 | `a9fb51f6cfcd68a2…` |

Week-open structure in this feed (S09 diagnostics, base parameters, report-only): EURUSD: 259 week opens, 31 gaps > 1.0 x ATR(14); GBPUSD: 258 week opens, 23 gaps > 1.0 x ATR(14); USDJPY: 216 week opens, 24 gaps > 1.0 x ATR(14); EURJPY: 260 week opens, 39 gaps > 1.0 x ATR(14).

### Pre-registered hourly hypotheses (fixed before the run)

- **S09 WeekendGapFadeSundayOpen** (1h, EURUSD, GBPUSD, USDJPY, EURJPY) — base parameters: gap_atr = 1.
  Mechanism: Weekend gaps are headline overreaction priced in the thinnest liquidity of the week (the Sunday 22:00 UTC open); dealers who quote the gap wide expect retracement once London liquidity returns on Monday. This is S08's mechanism with the trigger corrected to the feed's actual week boundary -- a new ID, not a re-run: S08 keyed on Monday groups preceded by a Friday bar, which this Sunday-open feed never produces.
  Falsifiable claim: Fading Sunday-open gaps larger than 1.0 x ATR(14) until Monday 12:00 UTC earns a mean of at least +0.25 x the gap size per event net of costs, across at least 40 gap events in total.
  Expected failure regime: Gaps caused by genuine regime events (elections, emergency central-bank action) where Monday continues in the gap direction.
- **S10 CalmRegimeHourlyMomentum** (1h, EURUSD, GBPUSD, USDJPY, EURJPY) — base parameters: lookback_hours = 120, vol_window_hours = 24, vol_median_hours = 1440.
  Mechanism: Hourly trends persist while volatility is orderly: benchmark execution and gradual information diffusion dominate. In volatility spikes the same horizon is liquidation and stop flow that whipsaws, so the momentum signal is only taken when realised volatility is at or below its trailing median.
  Falsifiable claim: 120 h momentum gated to calm regimes delivers a net-of-cost annualised Sharpe of at least 0.5 in at least 3 of the 4 pairs, with a positive mean period return.
  Expected failure regime: Calm regimes that are actually compression before a repricing (pre-announcement drift), where the gate admits the strategy exactly into the whipsaw.
- **S11 OverlapExpansionContinuation** (1h, EURUSD, GBPUSD, USDJPY, EURJPY) — base parameters: expansion_mult = 1.25, median_days = 20.
  Mechanism: A London morning range far above its recent norm means a real repricing is under way, not noise. The London/New York overlap brings the day's deepest liquidity, and US desks join the established direction (hedging and momentum allocation) more often than they fade it -- conditional on expansion, the opposite conditioning of S04's unconditional fade.
  Falsifiable claim: On expansion days the 13:00-17:00 continuation in the morning move's direction earns at least +0.15 x the morning range per signal day net of costs, on at least 60 signal days per pair.
  Expected failure regime: Expansion mornings caused by a single data print that is fully priced by noon, leaving the overlap to retrace it.
- **S12 IntradayAnchorReversion** (1h, EURUSD, GBPUSD, USDJPY, EURJPY) — base parameters: window_hours = 48, z_entry = 2, hold_bars = 4.
  Mechanism: Deviations of price from its trailing 48 h mean beyond two standard deviations are mostly positioning and liquidity overshoot rather than new information; the liquidity providers who absorbed the overshoot pull price back toward the anchor over the next few bars. Distinct from S05: a multi-day statistical anchor, not single-bar spike extremes.
  Falsifiable claim: The fade held for 4 bars earns a mean of at least +0.20 x ATR(20) per event net of costs, across at least 100 events per pair.
  Expected failure regime: Genuine repricing events (central-bank surprises) where the deviation is the new level and the anchor migrates to it.
- **S13 FridayProfitTakingReversal** (1h, EURUSD, GBPUSD, USDJPY, EURJPY) — base parameters: week_move_atr = 1.
  Mechanism: Leveraged positions built along the week's move are squared into the weekend to avoid gap risk; that squaring flow is one-sided against the week's direction in the last liquid hours of Friday, independent of fundamentals.
  Falsifiable claim: Fading a week move larger than 1.0 x ATR(14) over Friday 16:00-21:00 UTC earns at least +1 bp per held hour net of costs, on at least 40 signal weeks per pair.
  Expected failure regime: Weeks ending on major scheduled news where positions are deliberately carried (or added) over the weekend.

### Hourly results

| Hypothesis | Instrument | Trades | Net return* | Ann. Sharpe | PBO | DSR | Stressed net* | Verdict | Failed gates |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| S09 WeekendGapFadeSundayOpen | EURUSD | 30 | -1.34% | -0.22 | 0.30 | 0.00 | -1.78% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, DEFLATED_SHARPE, COST_STRESS_2X |
| S09 WeekendGapFadeSundayOpen | GBPUSD | 23 | +0.93% | 0.16 | 0.70 | 0.00 | +0.57% | FAIL | SAMPLE_SIZE, CSCV_PBO, DEFLATED_SHARPE |
| S09 WeekendGapFadeSundayOpen | USDJPY | 24 | -0.86% | -0.13 | 0.84 | 0.00 | -1.15% | FAIL | SAMPLE_SIZE, EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S09 WeekendGapFadeSundayOpen | EURJPY | 38 | +1.60% | 0.20 | 0.96 | 0.00 | +1.11% | FAIL | SAMPLE_SIZE, CSCV_PBO, DEFLATED_SHARPE |
| S10 CalmRegimeHourlyMomentum | EURUSD | 1220 | -25.61% | -1.00 | 0.67 | 0.00 | -43.33% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S10 CalmRegimeHourlyMomentum | GBPUSD | 1129 | -16.03% | -0.59 | 0.36 | 0.00 | -33.53% | FAIL | EXPECTANCY, WALK_FORWARD, DEFLATED_SHARPE, COST_STRESS_2X |
| S10 CalmRegimeHourlyMomentum | USDJPY | 759 | +7.58% | 0.33 | 0.94 | 0.01 | -1.74% | FAIL | CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S10 CalmRegimeHourlyMomentum | EURJPY | 1100 | +2.80% | 0.10 | 0.40 | 0.00 | -11.97% | FAIL | HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S11 OverlapExpansionContinuation | EURUSD | 363 | -7.47% | -0.70 | 0.49 | 0.00 | -12.75% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S11 OverlapExpansionContinuation | GBPUSD | 347 | -10.58% | -0.81 | 0.24 | 0.00 | -15.99% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S11 OverlapExpansionContinuation | USDJPY | 336 | -9.54% | -0.77 | 0.77 | 0.00 | -13.63% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S11 OverlapExpansionContinuation | EURJPY | 375 | -5.73% | -0.45 | 0.84 | 0.00 | -10.80% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S12 IntradayAnchorReversion | EURUSD | 922 | -14.24% | -0.71 | 0.53 | 0.00 | -27.64% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S12 IntradayAnchorReversion | GBPUSD | 938 | -13.61% | -0.57 | 0.53 | 0.00 | -28.17% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S12 IntradayAnchorReversion | USDJPY | 792 | -21.76% | -0.99 | 0.77 | 0.00 | -31.52% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S12 IntradayAnchorReversion | EURJPY | 978 | -20.93% | -0.85 | 0.00 | 0.00 | -34.19% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S13 FridayProfitTakingReversal | EURUSD | 218 | -3.34% | -0.59 | 0.74 | 0.00 | -6.49% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S13 FridayProfitTakingReversal | GBPUSD | 219 | -6.82% | -1.09 | 0.26 | 0.00 | -10.23% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, DEFLATED_SHARPE, COST_STRESS_2X |
| S13 FridayProfitTakingReversal | USDJPY | 187 | -3.79% | -0.73 | 0.61 | 0.00 | -6.09% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |
| S13 FridayProfitTakingReversal | EURJPY | 233 | -3.73% | -0.53 | 0.51 | 0.00 | -6.88% | FAIL | EXPECTANCY, WALK_FORWARD, HOLDOUT, CSCV_PBO, DEFLATED_SHARPE, COST_STRESS_2X |

\* Net return is the sum of per-period net returns (not compounded), the same quantity the fortress gates use.

### Hourly gate detail

### S09 WeekendGapFadeSundayOpen — EURUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 30857.000 | trades 30 < 100 |
| EXPECTANCY | FAIL | -0.221 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | PASS | 0.002 | holdout profitable |
| CSCV_PBO | PASS | 0.300 | PBO 0.300 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0001 below minimum |
| COST_STRESS_2X | FAIL | -0.018 | doubling costs wipes out the edge |

### S09 WeekendGapFadeSundayOpen — GBPUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 30737.000 | trades 23 < 100 |
| EXPECTANCY | PASS | 0.160 | positive expectancy |
| WALK_FORWARD | PASS | 0.800 | 0.800 of folds profitable |
| HOLDOUT | PASS | 0.005 | holdout profitable |
| CSCV_PBO | FAIL | 0.700 | PBO 0.700 above limit |
| DEFLATED_SHARPE | FAIL | 0.002 | DSR 0.0018 below minimum |
| COST_STRESS_2X | PASS | 0.006 | profitable at 2x costs |

### S09 WeekendGapFadeSundayOpen — USDJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 25768.000 | trades 24 < 100 |
| EXPECTANCY | FAIL | -0.129 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.003 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.843 | PBO 0.843 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0004 below minimum |
| COST_STRESS_2X | FAIL | -0.012 | doubling costs wipes out the edge |

### S09 WeekendGapFadeSundayOpen — EURJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | FAIL | 31127.000 | trades 38 < 100 |
| EXPECTANCY | PASS | 0.195 | positive expectancy |
| WALK_FORWARD | PASS | 0.800 | 0.800 of folds profitable |
| HOLDOUT | PASS | 0.005 | holdout profitable |
| CSCV_PBO | FAIL | 0.957 | PBO 0.957 above limit |
| DEFLATED_SHARPE | FAIL | 0.003 | DSR 0.0027 below minimum |
| COST_STRESS_2X | PASS | 0.011 | profitable at 2x costs |

### S10 CalmRegimeHourlyMomentum — EURUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | FAIL | -1.002 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.011 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.671 | PBO 0.671 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.433 | doubling costs wipes out the edge |

### S10 CalmRegimeHourlyMomentum — GBPUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.589 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | PASS | 0.000 | holdout profitable |
| CSCV_PBO | PASS | 0.357 | PBO 0.357 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.335 | doubling costs wipes out the edge |

### S10 CalmRegimeHourlyMomentum — USDJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.334 | positive expectancy |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | PASS | 0.014 | holdout profitable |
| CSCV_PBO | FAIL | 0.943 | PBO 0.943 above limit |
| DEFLATED_SHARPE | FAIL | 0.010 | DSR 0.0101 below minimum |
| COST_STRESS_2X | FAIL | -0.017 | doubling costs wipes out the edge |

### S10 CalmRegimeHourlyMomentum — EURJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | PASS | 0.102 | positive expectancy |
| WALK_FORWARD | PASS | 0.600 | 0.600 of folds profitable |
| HOLDOUT | FAIL | -0.004 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.400 | PBO 0.400 within limit |
| DEFLATED_SHARPE | FAIL | 0.001 | DSR 0.0011 below minimum |
| COST_STRESS_2X | FAIL | -0.120 | doubling costs wipes out the edge |

### S11 OverlapExpansionContinuation — EURUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.702 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.005 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.486 | PBO 0.486 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.128 | doubling costs wipes out the edge |

### S11 OverlapExpansionContinuation — GBPUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.809 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.032 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.243 | PBO 0.243 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.160 | doubling costs wipes out the edge |

### S11 OverlapExpansionContinuation — USDJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.772 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.010 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.771 | PBO 0.771 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.136 | doubling costs wipes out the edge |

### S11 OverlapExpansionContinuation — EURJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.445 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.008 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.843 | PBO 0.843 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.108 | doubling costs wipes out the edge |

### S12 IntradayAnchorReversion — EURUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.708 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.017 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.529 | PBO 0.529 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.276 | doubling costs wipes out the edge |

### S12 IntradayAnchorReversion — GBPUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.566 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.018 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.529 | PBO 0.529 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.282 | doubling costs wipes out the edge |

### S12 IntradayAnchorReversion — USDJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.985 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.013 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.771 | PBO 0.771 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.315 | doubling costs wipes out the edge |

### S12 IntradayAnchorReversion — EURJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.847 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.000 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.000 | PBO 0.000 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.342 | doubling costs wipes out the edge |

### S13 FridayProfitTakingReversal — EURUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30857.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30857.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.595 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.008 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.743 | PBO 0.743 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.065 | doubling costs wipes out the edge |

### S13 FridayProfitTakingReversal — GBPUSD

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 30737.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 30737.000 | sample size sufficient |
| EXPECTANCY | FAIL | -1.091 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.000 | only 0.000 of folds profitable |
| HOLDOUT | FAIL | -0.016 | holdout total and Sharpe must both be positive |
| CSCV_PBO | PASS | 0.257 | PBO 0.257 within limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.102 | doubling costs wipes out the edge |

### S13 FridayProfitTakingReversal — USDJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 25768.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 25768.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.727 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.400 | only 0.400 of folds profitable |
| HOLDOUT | FAIL | -0.024 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.614 | PBO 0.614 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.061 | doubling costs wipes out the edge |

### S13 FridayProfitTakingReversal — EURJPY

| Gate | Result | Metric | Reason |
|---|---|---:|---|
| DATA_INTEGRITY | PASS | 31127.000 | series is structurally sound |
| SAMPLE_SIZE | PASS | 31127.000 | sample size sufficient |
| EXPECTANCY | FAIL | -0.525 | total, mean and annualized Sharpe must all be positive |
| WALK_FORWARD | FAIL | 0.200 | only 0.200 of folds profitable |
| HOLDOUT | FAIL | -0.009 | holdout total and Sharpe must both be positive |
| CSCV_PBO | FAIL | 0.514 | PBO 0.514 above limit |
| DEFLATED_SHARPE | FAIL | 0.000 | DSR 0.0000 below minimum |
| COST_STRESS_2X | FAIL | -0.069 | doubling costs wipes out the edge |

## Multiple-testing accounting

The deflated Sharpe gate is charged for **76 trials programme-wide**: Sprint 1's 24 + Sprint 2's 32 + this sprint's 20 new evaluations. The trial-Sharpe population pools all sprints' base runs. Sprint 1's Sharpes could NOT be reconstructed in this run (FRED unreachable / carry inputs unavailable); their count of 24 is still charged in n_trials, and the dispersion population is Sprint 2's 32 + this sprint's base runs — a documented deviation, identical in spirit to Sprint 2's fallback. No prior verdict was recomputed; reconstructed Sharpes feed only the multiple-testing correction.

## Conclusions

**No Sprint 3 evaluation passed the fortress.** The programme total is now 76 trials with no survivor. Per the roadmap, the correct response is not to loosen the fortress or re-tune these IDs — every rejected hypothesis stays rejected under its ID, and any follow-up idea gets a new ID and increases the trial count.

INR remains **PENDING_DATA** after this run — not because no series exists (DEXINUS does, in the same H.10 family Sprint 1 already used), but because this run could not complete the fetch-and-validate step. That distinction matters: the blocker is now an endpoint outage / failed check with a known remedy, not an absence of data.

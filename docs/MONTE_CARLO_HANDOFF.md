# Native Monte Carlo validation handoff

## Implemented research 1.1 extension

ResearchPlan 1.1 requires a MonteCarloSpec declared before training and held-out evaluation. It records seed, iterations, block length, minimum completed trades, lower-tail percentile, required fraction of positive simulations, drawdown limit and its quote currency. Existing 1.0 plans retain a pending Monte Carlo gate; they cannot acquire retrospective evidence or change their plan under the same experiment identifier.

The native adapter binds its output to the complete native result hash, dataset fingerprint, genome hash, implementation hash and P&L currency. It constructs flat-to-flat trade cash flows from actual native fill prices, quantities and fees. Partial exits belong to the same trade cycle. Long and short closed cycles are supported; a reversal is rejected until fee attribution is explicitly implemented. Flat-ended closed-cycle totals must reconcile exactly to native marked P&L, and the reconstructed end position must match the native position.

An unfinished position produces PENDING/UNCLOSED_POSITION. Too few completed trades or a block longer than the sample produces PENDING/INSUFFICIENT_CLOSED_TRADES. No simulated quantiles or hashes are invented for these states.

## Declared computation

The sampler chooses circular contiguous blocks with replacement until each simulation contains the original number of completed trades. SHA-256 counter draws with integer rejection sampling choose block starts, independently of Python's global random state. All arithmetic uses a fresh 28-digit Decimal context. A simulation records ending cash-flow P&L and peak-to-trough cash-flow drawdown at trade closes. Nearest-rank quantiles provide lower-tail P&L, median P&L and 95th-percentile drawdown; positive_fraction is the empirical fraction of simulations with strictly positive ending P&L. The ordered simulation outcomes have a SHA-256 checksum.

The declared screen passes only when lower-tail P&L is nonnegative, drawdown is within its currency-denominated limit, and positive_fraction reaches the declared threshold. Report construction and archive retrieval recompute this evidence from the stored native holdout result, preventing a changed statistic or gate from silently passing. Promotion remains false; adversarial, sandbox, independent review and shadow gates remain pending.

Circular block resampling is the background technique named in [Politis and Romano's Stanford technical report](https://statistics.stanford.edu/technical-reports/circular-block-resampling-procedure-stationary-data). This implementation does not establish stationarity, choose a statistically optimal block size, compute an alpha-test p-value, adjust for multiple candidate/seed searches or validate regime changes. Its thresholds and minimum sample are declared engineering screens, not calibrated acceptance criteria.

Drawdown_basis is explicitly TRADE_CLOSE_CASHFLOW. It omits intra-trade market-path drawdown and cannot replace portfolio risk or a continuous marked equity curve. Resampling existing closed outcomes does not replay historical prices, depth, latency or changed execution costs. The existing separate native cost-stress runs remain required.

## CLI and evidence

```powershell
ai-quant-research tests/fixtures/research_mc_development.csv tests/fixtures/research_mc_holdout.csv --experiment-id synthetic-mc-baseline --hypothesis-id synthetic-mc-interface --description "Synthetic cyclic fixture for native Monte Carlo integration; no alpha claim" --candidate 1:2 --candidate 2:3 --train-bars 20 --validation-bars 20 --quantity 100 --fee 0.10 --stress-fee 0.20 --stress-slippage-bps 10 --monte-carlo --mc-iterations 100 --workspace data-local/research-mc-a --output docs/evaluations/research-monte-carlo-baseline.json
python -m pytest -q tests/test_monte_carlo.py tests/test_research_pipeline.py
```

Monte Carlo options require --monte-carlo and are saved in the plan. --mc-seed, --mc-iterations, --mc-block-size, --mc-drawdown-limit and --mc-currency can override defaults before execution. The same-registry holdout reuse and immutable-plan rules continue to apply.

The synthetic baseline has sixty development bars and four hundred held-out bars. Twelve native runs produced twenty fully completed held-out trade cycles; the one-hundred-iteration screen passed, while the overall report stayed VALIDATION_INCOMPLETE. Two fresh workspaces produced byte-identical exports. Typed recomputation and the research-result-v1.1 JSON Schema validated the artifact. This fixture has no real-market or alpha claim.

Tests cover native fees and partial exits, short cash flows, reversal rejection, positive/negative samples, insufficient/unclosed samples, deterministic seeds, caller Decimal traps, invalid policies, full native integration, modified-result rejection, declared CLI policy and unchanged legacy canonical hashes. Monte Carlo computations are capped at 500,000 trade observations; iterations are 100..2,000, block length 1..32 and minimum completed trades is at least twenty. Native pipeline checks the worst-case observation budget before execution.

## Compatibility and remaining work

Canonical 1.0 encoding omits the new null extension fields, preserving existing hypothesis/plan/report hashes. A previously saved 1.0 SQLite report was retrieved with its original canonical hash. Version 1.1 includes the full policy and evidence; older packages do not understand it. Algorithm changes require a new method contract and compatibility review. Preserve databases, raw/Parquet sources, strategy source objects, schemas and exports before rollback.

Statistical overfit/multiple-testing checks, adversarial scenarios, isolated builders, shadow evidence, reviewed calibration and promotion governance remain unfinished. Native tick reversal attribution and intra-trade drawdown models remain open. No provider, broker connection or capital authority was added.

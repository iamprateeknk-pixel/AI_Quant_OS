# Native directional bar replay

The opt-in `run_nautilus_directional_backtest` submits actual Nautilus orders for targets -1, 0 and +1. A target is computed from prior completed bars and executes at the next bar's synthetic open quote. The position equals target times the configured unit quantity. Complete reversals place one order for twice that quantity; the fixed fee applies once to that order.

The new replay model is `BAR_OPEN_CLOSE_LONG_SHORT_V1`. The existing long/flat bar runner keeps its original model, behavior and registered implementations. The shared verifier reconciles every fill, adverse quote rounding, fixed fee, position and final close mark against retained source bars. PnL is exact fill cash flows plus final close inventory value. The mark is a diagnostic valuation, and no terminal liquidation is added.

## Run locally

```powershell
ai-quant-backtest tests/fixtures/nautilus_replay.csv --strategy moving-average-directional --fast-window 1 --slow-window 2 --quantity 10 --slippage-bps 10 --fee 0.10 --output data-local/directional-result.json
ai-quant-research tests/fixtures/research_development.csv tests/fixtures/research_holdout.csv --strategy moving-average-directional --candidate 1:2 --train-bars 6 --validation-bars 4 --quantity 100 --fee 0.10 --stress-fee 0.20 --stress-slippage-bps 10 --experiment-id directional-example --hypothesis-id directional-example --description "Synthetic interface evaluation" --workspace data-local/directional-research --output data-local/directional-research.json
python -m pytest -q tests/test_nautilus_directional.py
```

`moving-average-directional@1.0.0` is a trusted sample with separate immutable source provenance. It compares exact cross-multiplied moving averages, uses flat for insufficient history or equal averages, long for rising averages and short for falling averages. Windows require 1 <= fast < slow <= 4096. The sample has no trading-performance claim.

Synthetic top-of-book size remains 1,000,000 units on each side. Directional quantity is capped at 500,000 so a reversal fits completely. Initial margin and currency precision checks can reject otherwise valid quantities. This is one independent instrument per engine; EURUSD, GBPUSD, USDJPY and EURJPY have verified short accounting in their own quote currencies. A shared-collateral multi-instrument simulation remains unfinished.

## Research and retained evidence

Research 1.0 supports directional walk-forward selection, frozen holdout and increased-cost evaluation. Candidate dossiers, counterfactual size/parameter/cost comparisons and cumulative forward shadow diagnostics preserve the parent's replay model. Research 1.1/1.2 refuses this strategy before reserving a holdout because reversal fee allocation in Monte Carlo and directional adversarial policy require review. All five remaining 1.0 gates stay pending, and reports cannot authorize promotion.

- [Native fixture](evaluations/nautilus-directional-baseline.json) retains eight source bars, quantity and a fully source-bound registered result. Its actual fills are BUY 10, SELL 20, BUY 20; exact final marked PnL is -5.3610 USD with ten-basis-point adverse spread and 0.10 USD per-order fees. Two independent engines reproduced identical exports.
- [Research fixture](evaluations/research-directional-baseline.json) retains a canonical 1.0 archive. Two fresh workspaces reproduced identical research reports; synthetic holdout PnL is 9.8 USD and five promotion gates remain pending. This is interface/accounting evidence, not held-out real-market performance.
- [Evaluation schema](schemas/nautilus-directional-evaluation.schema.json) describes the retained native fixture. Existing native/research/candidate/comparison/shadow schemas accept the additive replay model and serialized Decimal exponent notation. Earlier artifact bytes and implementation hashes are preserved.

The four-instrument short checks, causal histories, fees, reversals, invalid targets, liquidity bound, caller precision isolation, registered command, frozen research, restart archives, counterfactual reproduction and shadow prefixes are exercised in `tests/test_nautilus_directional.py`. Windows/Linux native jobs include this test file; hosted execution remains unverified.

Remaining P03 work includes catalog selection, historical depth/latency, partial liquidity, integrated multi-instrument simulation, process isolation for untrusted strategies and independent critical review. Paper/live broker routing is separate. Rollback preserves archived reports and source objects; code without the new model cannot decode directional reports.

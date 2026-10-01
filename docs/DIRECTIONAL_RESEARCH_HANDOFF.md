# Directional research statistics

Research 1.1 and 1.2 can now evaluate the native directional bar strategy when its reversal-fee policy is explicitly declared. Existing plans, source manifests and archived report hashes retain their original defaults and bytes.

## Fee policy

`CIRCULAR_EQUAL_REVERSAL_HALF_FEE_BLOCKS_SHA256_V1` selects the existing seeded circular block sampler with `EQUAL_SIZE_HALF_FEE_V1` trade accounting. One native reversal order closes Q units and opens Q units in the opposite direction. Half of its actual fixed fee belongs to the closing cycle and half to the new cycle. The closing PnL enters the resampling sample; the opening half-fee stays with the open cycle until it closes. The fee is conserved exactly and never charged twice.

Partial exits within a cycle and independent long/short entries still work. Unequal reversal legs are rejected. Allocation is analytical Decimal accounting: a half-cent or half-yen is retained exactly and does not invent two broker charges. Arithmetic which cannot be represented exactly within the declared 28-digit context is rejected. Open terminal positions keep Monte Carlo pending; the sample excludes unrealized marks.

Default `CIRCULAR_CLOSED_TRADE_BLOCKS_SHA256_V1` still rejects reversals. Directional research with that default is refused before reserving a holdout. `--mc-method` requires `--monte-carlo`; there is no implicit policy upgrade. Directional computation budgets allow a closed cycle on every bar, rather than the legacy long/flat bound of one per two bars.

## Run locally

```powershell
ai-quant-research tests/fixtures/research_mc_development.csv tests/fixtures/research_directional_holdout.csv --strategy moving-average-directional --candidate 1:2 --experiment-id directional-statistics --hypothesis-id ma-fixture --description "Synthetic moving-average interface evaluation" --train-bars 20 --validation-bars 20 --quantity 100 --fee 0.10 --stress-fee 0.20 --stress-slippage-bps 10 --monte-carlo --mc-iterations 100 --mc-method CIRCULAR_EQUAL_REVERSAL_HALF_FEE_BLOCKS_SHA256_V1 --adversarial --workspace data-local/directional-statistics --output data-local/directional-statistics.json
python -m pytest -q tests/test_directional_research.py
```

Adversarial replay uses existing declared missing-observation and downward-step datasets, the frozen implementation and increased costs. A shared native verifier checks each scenario's source-open fill time/price, adverse rounding, fixed fees, reversal quantity, allowed position and final marked cash flow. Each scenario must match the baseline replay model and occur in archived attempts.

## Retained evidence

[The saved experiment](evaluations/research-directional-statistics-baseline.json) includes raw/archive source lineage, retained holdout bars and a canonical report hash. It has 60 development bars, 402 holdout bars, one genome, two folds and 11 native attempts. The final two holdout bars explicitly flatten the sample; the engine inserts no terminal liquidation. Two fresh command workspaces exported identical bytes. Exact result-hash replay uses the retained Parquet-derived Decimal representation of the source marks.

Actual synthetic holdout PnL is 236.9 USD across 40 completed cycles. The 100-simulation lower-tail PnL is 235.95 USD. Missing-observation and downward-step PnLs are 224.702 and 221.109 USD. Both declared screens pass. Sandbox, independent review and forward-shadow approval remain pending; promotion stays false.

These are interface/accounting checks on synthetic observations. Cash-flow drawdown is measured at trade closes and omits intratrade drawdown. Resampling does not replay new market paths, latency, depth or multiple regimes, establish alpha, calibrate thresholds or correct repeated candidate searches. Remaining P08 generation/sandbox/overfit/advanced stress/lifecycle work and release gates remain open.

Rollback preserves the research database, source archives and exports. Old code cannot decode the new method value. Existing method/report hashes and sample defaults are preserved.

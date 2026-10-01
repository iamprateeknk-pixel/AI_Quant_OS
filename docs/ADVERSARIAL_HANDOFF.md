# Native adversarial research screens

P08 remains in progress. Research contract 1.2 adds two predeclared native scenarios to the existing development-only selection, walk-forward, holdout, cost-stress and Monte Carlo evidence.

## Execution and evidence

- Register `AdversarialSpec` in the immutable plan before training. Version 1.2 also requires its Monte Carlo policy. Changing either policy requires a new experiment with unused, chronologically separated data under the existing holdout reservation rules.
- Freeze the winner using development data. Only then perturb the reserved holdout and execute the same registered genome/source binding through NautilusTrader, using the declared stress costs and quantity.
- `MISSING_OBSERVATIONS` removes every Kth bar, retaining timestamps of remaining observations. K is an integer from 2 through 16; the source needs at least 2K bars. This checks strategy behavior with missing observations. Production feed rejection, clock integrity and gap repair remain separate requirements.
- `DOWNWARD_STEP` applies a permanent price reduction starting at the middle observation. The declared shock is 1 through 5,000 basis points. Each OHLC value is rounded to the registered FX price precision using half-even rounding. Volume and timestamps are retained. A perturbation that cannot produce valid positive prices fails execution.
- Retain original holdout bars and both native results. Decoding regenerates perturbations and checks source/scenario fingerprints, candidate/source identity, instrument, replay model, costs, quantity, actual fees, final position, order activity and cash flows plus the final close mark. Scenario results must occur in the archived attempt list.
- Fail when either scenario's final net P&L is below the negative declared maximum loss in the native quote currency. Otherwise, any scenario with zero orders remains pending; both active scenarios within the loss budget pass this screen. These are engineering thresholds, not calibrated statistical claims.
- Keep the existing native limits: 128 executions and 20,000 total bar visits per experiment, with both scenario executions included conservatively. Native scenario failure archives its stage and every previously completed attempt.

## Compatibility and remaining gates

Version 1.0 and 1.1 reports keep their original encoding and hashes; their adversarial gate stays pending. Original schemas remain available. New exports use `docs/schemas/research-result-v1.2.schema.json` and retain raw/archive source lineage.

The committed synthetic baseline has 60 development bars, 400 holdout bars, two genomes, two folds and 14 native executions. Two fresh workspaces reproduced its complete export exactly. Its adversarial and Monte Carlo screens pass, while sandbox, independent review and shadow evidence remain pending. Promotion is always false.

This is a partial adversarial suite. Spread/depth outages, latency, partial liquidity, price spikes, multiple regimes, multi-instrument shocks and live feed/control integration remain. Final marked P&L does not bound intratrade drawdown. The local archive does not establish security against a host administrator. The synthetic fixture demonstrates neither alpha nor live readiness.

## Run the synthetic fixture

```powershell
ai-quant-research tests/fixtures/research_mc_development.csv tests/fixtures/research_mc_holdout.csv --experiment-id adversarial-baseline --hypothesis-id adversarial-fixture --description "Synthetic native perturbation interface evaluation" --candidate 1:2 --candidate 2:3 --train-bars 20 --validation-bars 20 --quantity 100 --fee 0.10 --stress-fee 0.20 --stress-slippage-bps 10 --monte-carlo --mc-iterations 100 --adversarial --adversarial-loss-limit 0 --workspace data-local/research-adversarial --output data-local/research-adversarial.json
```

Defaults: drop every fifth observation, a 1,000 bps downward step, maximum loss 100 USD. Optional flags: `--adversarial-drop-every`, `--adversarial-shock-bps`, `--adversarial-loss-limit`, `--adversarial-currency` (USD or JPY). They require `--adversarial`, which requires `--monte-carlo`. Currency must match the native instrument's quote currency. Export success is not candidate approval.

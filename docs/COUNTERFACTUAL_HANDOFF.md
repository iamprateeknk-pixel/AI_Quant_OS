# Candidate counterfactual diagnostics

This P09 increment compares declared alternatives with a frozen candidate's original native holdout result. The data have already been observed. The report is diagnostic evidence and always has `promotion_allowed=false`; it cannot clear research gates, reactivate withdrawn candidates or enter paper/live operation.

## Replay and isolation

The input retains the complete verified candidate dossier, original source objects, normalized holdout bars, source fingerprint, original report hash and ordered alternative declarations. One to seven alternatives can change explicit parameters, integer size or costs for the same versioned built-in strategy. Duplicate configurations, undeclared default parameters, alternative source implementations and source substitutions are refused. At most eight native engines and 20,000 bar visits are allowed per replay; holdouts have at most 4,096 bars.

The original baseline executes first and must reproduce the complete archived native result and binding. Each subsequent alternative creates and disposes its own native BacktestEngine account. It has no platform paper broker, portfolio, risk approval, agent tools or candidate lifecycle writes. This is simulation state isolation: trusted built-in Python runs in the current process, so it is not a security sandbox for malicious or generated code.

Rows retain fills, costs, size, genome/source binding and PnL difference from the original baseline. Decoding checks declared order, quantity, fees, source-open execution prices with explicit FX rounding, long/flat positions and exact fill cash flows plus final-close marking. Decimal comparisons use an isolated 28-digit context and reject unintended inexact arithmetic. Native realized PnL is retained as native evidence; it is not an independently recalculated diagnostic measure.

## Persistence and failures

`counterfactual.sqlite3` is a separate sibling database. An immediate transaction reserves the complete request before execution. Only its private owner can finish once. Repeated completed requests return checked cached evidence; changed requests under the same ID are refused. Unfinished reservations are uncertain and require investigation, never blind reruns. Concurrent requests have one owner.

Source preparation failure, native execution failure and baseline reproducibility failure have sanitized codes. Successfully completed prior runs are retained. A baseline mismatch stops all alternatives and retains the differing baseline. Invalid result evidence or storage failures leave an uncertain reservation for investigation. No partial result is presented as a completed replay.

Canonical artifacts are SHA-256 checked, typed and bound to reserved inputs on read. Limits are 16 MiB per artifact, 1,000 reservations and 64 MiB aggregate input/report bytes. Preserve the SQLite database and WAL during recovery. These checks detect local corruption; they do not prevent a host administrator from replacing data and hashes or replacing the entire database. There is no external attestation or cross-workspace replay quota.

## Usage

Install the editable package with the existing research/trading prerequisites. Register the completed research candidate first, then run:

```powershell
ai-quant-counterfactual --workspace data-local/research-adversarial-a --candidate-id aa8c5d53f15133b8b1b391af0123a9b50cd047e067112e989fa56bef5126d4c6 --replay-id compare-sizing-costs --variants docs/evaluations/counterfactual-variants.json --output data-local/counterfactual-report.json
```

The example declarations include complete genomes, quantity and costs. This is a local command, with no new HTTP or agent tool permission. Archives without retained adversarial source bars require `--holdout original-holdout.csv`; the normalized source must exactly match the frozen holdout scope and fingerprint. The exported report retains normalized bars, not the original CSV byte representation. Keep original data archives for independent raw-source lineage.

Exports use immutable atomic publication; exact repeats succeed, changed content fails. Database/WAL/SHM targets, input files and archive/strategy subtrees are protected. A failed export can be retried to a valid destination without rerunning the stored replay.

## Evidence and remaining scope

Two independent workspaces each ran four native engines and exported identical `docs/evaluations/counterfactual-baseline.json`. Typed/canonical replay and `docs/schemas/counterfactual-report.schema.json` validated. The retained synthetic 400-bar EURUSD fixture produced:

| Variant | Net PnL (USD) | Difference from baseline (USD) |
| --- | ---: | ---: |
| BASELINE | 116.00 | 0.00 |
| half-size | 56.00 | -60.00 |
| high-cost | -684.20 | -800.20 |
| slower-average | 115.00 | -1.00 |

This fixture establishes replay reproducibility and sensitivity under the declared model. It does not establish forward performance, causal attribution, real broker slippage or suitability for trading. No paid provider or live broker was exercised. Windows/Linux native CI includes the new tests but hosted runs remain unverified.

P09 still needs live/shadow comparison, candidate-bound paper assignments, agent attribution, fill-time/latency evidence and reality-gap alerting. Holdout-to-forward shadow demotion is implemented as a local candidate diagnostic; it does not satisfy PAPER-versus-shadow comparison. Daily opening marks and durable accounting reports are available through the separate daily-evidence workflow; their linkage to counterfactual and shadow results remains. P08 sandbox, independent review and shadow gates remain pending.

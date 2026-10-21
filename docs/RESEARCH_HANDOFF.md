# Research pipeline handoff

Completed experiment dossiers now have an immutable local registry, source-retaining bundle export and protected API. See [candidate registry handoff](CANDIDATE_REGISTRY_HANDOFF.md) for lifecycle, retention and the unfinished champion/promotion requirements.

Version 1.2 adds predeclared missing-observation and downward-price-step native screens. See [adversarial handoff](ADVERSARIAL_HANDOFF.md) for evidence, compatibility and remaining scenario coverage.

Version 1.3 adds an opt-in candidate-rank diagnostic and a combinatorially symmetric cross-validation estimate over the retained candidate-by-validation-fold net-P&L matrix. See [ADR 0206](adr/0206-research-selection-rank-diagnostic.md) for its scope and limitations.

## Implemented P08 slice

The local research runner executes actual registered Nautilus bar backtests for a predeclared hypothesis and bounded strategy candidate set. It records full configuration/source bindings, normalized dataset fingerprints, UTC data scopes, native fills and positions, costs and every completed native attempt. It has no model, broker connection, Risk Kernel approval or promotion authority.

The CLI reads development and terminal holdout CSVs as separate bounded byte snapshots, publishes them into the checksum-verified raw/Parquet BarArchive, and replays the archived objects. Hypotheses and experiment plans are immutable by identifier in a separate SQLite database. Changed plans or hypothesis descriptions cannot reuse an existing identifier.

## Selection and data separation

EXPANDING_COLD_START_V1 uses all initial train_bars for the first training window, the following validation_bars for evaluation, then expands training over observations that are now in the past. Validation blocks do not overlap; incomplete trailing blocks are rejected. Each backtest starts a fresh account and empty strategy history. There is no carried position or warm-up from training into validation/holdout.

For each fold, every candidate is evaluated only on that fold's training dataset. The winner has highest exact native fill/terminal-mark net P&L; a genome SHA-256 tie-break is deterministic. Versions 1.0–1.2 run only that winner on the next validation block and its adverse cost scenario. Version 1.3 also runs the full predeclared candidate set on the same validation block, retains those results and reports the training winner's descending validation rank with deterministic tie-breaking. It then enumerates unique equal-sized CSCV splits of the candidate-by-fold net-P&L matrix, selecting on one half and ranking on the other, and reports the fraction of selected candidates below the validation median. At least four even-count validation folds are required. These extra runs are diagnostic only and do not alter the selected winner or measured gates. Finally, all candidates train on the complete development dataset. That final winner and its training evidence are durably frozen before any terminal holdout execution. Validation observations can enter later training as past development data; the terminal holdout never enters selection.

Version 1.3 requires the already-declared Monte Carlo and adversarial policies and is enabled from the CLI with `--selection-diagnostics`. Its additional native calls and bar visits are included in the preflight budget; an over-capacity plan is rejected before reservation or execution. Its CSCV probability uses aggregate P&L over equal-length validation blocks. It is not a p-value or a general correction for trials performed outside this experiment or repeated seeds/experiments. The report remains evidence-only and `promotion_allowed=false`.

The database transaction reserves a terminal holdout instrument/time interval before training. A different experiment cannot reuse an overlapping holdout period, relabel it with changed prices, train on a reserved terminal period, or designate previously exposed development observations as a new terminal holdout. These checks are serialized across connections. Exact-plan retries retrieve the verified archived report. Interrupted reservations require explicit investigation and are not rerun automatically.

This isolation applies to this pipeline and its shared registry. Trusted Python infrastructure can read both datasets; it is not an operating-system sandbox. An administrator can bypass the local registry by starting a different database. P07 generator/validator resource permissions, a centrally governed dataset catalog and isolated builder processes remain to be connected.

## Scorecard and archival behavior

Four declared screens check nonnegative aggregate walk-forward marked P&L, nonnegative holdout marked P&L, nonnegative stressed validation/holdout marked P&L, and actual validation/holdout order activity. Stress fees and slippage cannot be lower than baseline, and at least one must be higher. Native bar costs retain their documented synthetic quote model.

These screens are engineering evidence, not proof of an investable edge. Independent fold accounts and end marks are not one continuous portfolio return. In version 1.0, Monte Carlo, adversarial, sandbox, independent review and shadow gates are mandatory PENDING entries. No report can skip required gates or set promotion_allowed=true. Outcomes are FAILED, REJECTED or VALIDATION_INCOMPLETE; none is promotion-ready.

Candidate construction and native failures produce sanitized stage reasons and retain completed attempts in the immutable report. Failed screens produce a REJECTED report with reasons. Invalid or already exposed input scopes are rejected before execution. SQLite reads verify canonical typed plan/report/selection hashes and their hypothesis/holdout lineage. Completion requires the reservation's private ownership token and commits once. The store is local integrity checking, not independently immutable audit storage or a publisher signature.

## Commands and evidence

On Windows, immutable raw/Parquet archive publication first uses a hard link. If
the OS denies hard-link creation with access denied, the writer uses the same-volume
Windows atomic no-replace rename; conflicting existing bytes remain a hard error.
The fallback is covered by `tests/test_data_archive.py` and does not weaken the
archive's immutable-object contract. See ADR 0205.

```powershell
python -m pip install -e ".[dev,data,trading]"
ai-quant-research tests/fixtures/research_development.csv tests/fixtures/research_holdout.csv --experiment-id synthetic-ma-baseline --hypothesis-id synthetic-ma-interface --description "Synthetic fixture for pipeline verification; no trading-performance claim" --candidate 1:2 --candidate 2:3 --train-bars 6 --validation-bars 4 --quantity 100 --fee 0.10 --stress-fee 0.20 --stress-slippage-bps 10 --workspace data-local/research-baseline-a --output docs/evaluations/research-native-baseline.json
python -m pytest -q tests/test_research_pipeline.py
```

The checked-in baseline uses synthetic monotonic EURUSD prices: fourteen development bars, six held-out bars, two candidate genomes and two walk-forward folds. Two fresh workspace runs produced byte-identical reports across twelve native attempts. Typed decoding, report SHA-256 and the saved JSON Schema were verified. It is an interface fixture with no real-market accuracy, alpha or calibration claim. Raw fixture bytes are preserved across Git checkout line-ending conversion.

The v1.3 interface fixture at `docs/evaluations/research-cscv-baseline.json` uses four synthetic validation folds and two candidates. Its CSCV estimate is 1/3. The report is `REJECTED` because some folds have no activity; it is not evidence of a trading edge. The saved v1.3 JSON Schema, typed report decoding, canonical report digest and round-trip were verified.

Runtime limits are 16 candidates, 16 folds, 4,096 bars per input, 128 native calls and 20,000 total bar visits per experiment, 4 MiB per CLI CSV, 16 MiB per archived report and 1,000 experiments per database. CLI exports include separate source lineage and a canonical report hash. Success of the export command means the report was produced; inspect outcome and gates for its evaluation state.

## Monte Carlo extension

Version 1.1 adds an explicitly predeclared native closed-trade resampling screen; insufficient or unclosed samples remain pending. Version 1.0 archives and pending gates keep their existing meaning. See [Monte Carlo handoff](MONTE_CARLO_HANDOFF.md) for scope, declared policy, statistics, tests and compatibility.

## Remaining work and rollback

Automated hypothesis generation, general strategy genomes, isolated sandbox building, independent validation and correction across external or repeated-seed searches, broader adversarial suites, shadow evidence, champion/challenger governance, promotion bundles, agent/API/UI integration, production observability and independent review remain unfinished. P08 is in progress.

Preserve the research database, raw/Parquet archive, strategy registry and result exports before rollback. The new research tables are separate from paper orders and controls. No automatic deletion, retention migration, reservation recovery or holdout release is implemented; abandoned holdout claims remain conservative reservations. Older packages cannot read these new contracts. Hosted Windows/Linux research checks are configured but have not run.

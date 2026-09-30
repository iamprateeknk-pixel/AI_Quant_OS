# ADR 0009: Benchmark and calibration evidence

## Decision

Store descriptive classifier evaluation reports independently from approval policy. Schema 1.0 reports have EVIDENCE_ONLY status. Their existence, metrics and references cannot lower gateway escalation, issue a risk decision or change a trading limit.

Evaluation records bind sample ID, input/world-state reference, instrument, held-out label reference, expected classification, primary prediction/confidence, outcome and measured latency. Invalid schemas, non-finite probabilities, negative latency, duplicate sample IDs and duplicate input/instrument pairs are rejected. Ground-truth validity and holdout independence require external review; reference strings are not proof of either.

## Metrics

Accuracy is measured only across valid non-abstaining primary predictions. Coverage is valid predictions divided by all attempts; the correct fraction of all attempts is reported separately. Abstentions, timeouts, failures, invalid outputs and expiry remain counted.

Ten equal-width reliability bins group declared confidence. Bins use [lower, upper), with confidence 1 included in the final bin. Each carries count, mean confidence and empirical correctness. Expected calibration error is the weighted absolute gap between bin accuracy and confidence. This follows the reliability/ECE approach described by [Guo et al., ICML 2017](https://proceedings.mlr.press/v70/guo17a.html), with explicitly documented bin boundary conventions.

Top-label Brier is the mean squared difference between declared confidence and the binary correctness indicator. It is not the multiclass Brier score, since candidates do not supply full class distributions. Latency mean includes all attempts; p95 uses nearest-rank ceil(0.95 * n). Missing scored predictions yield null accuracy/calibration metrics. No probability fitting or temperature scaling is performed.

Calculations use Decimal with precision 28 and half-even rounding. Record order is canonicalized; the complete report contents, metadata and creation timestamp produce the content-derived report ID. Identical observations and metadata reproduce the ID; repeated benchmark runs naturally have different measured latencies and timestamps.

## Harness

The asynchronous benchmark invokes the existing DecisionGateway, preserving schema, evidence and TTL validation. Labels are held in BenchmarkCase and are not passed to the classifier. Replay uses a logical clock anchored at each archived snapshot's as_of and advanced by measured monotonic elapsed time. Historical adapters must respect this logical clock in response timestamps. Expiry during evaluation is counted as EXPIRED. A primary failure is never scored as a successful quantitative fallback prediction.

The harness accepts a configured FastClassifier adapter and runs serially. No production Jev adapter or benchmark dataset is bundled, and no real Jev performance has been measured. Fixture tests validate the harness; they are not model-quality evidence.

## Persistence and interfaces

CalibrationStore writes immutable content-addressed reports in SQLite, rejects conflicting writes, verifies stored byte hashes, validates schemas and recomputes all metrics on reads. A whole-database rewrite/recomputed hash attack remains outside local hash protection. Independent protected artifact storage/checkpoints remain necessary.

Authenticated POST /api/v1/paper/decisions/calibration computes and stores a report from supplied observations and audits the report ID/provider/dataset reference. GET /api/v1/calibration/{report_id} reads a verified report, returning 404 for absence and 503 for integrity failure. API batches are bounded to 10,000 records. Report storage and audit writes are separate transactions: a failed audit can leave a stored evidence artifact without an audit event, requiring investigation before policy review.

The CLI builds or verifies a JSON report and optionally stores it. Output creation is exclusive. If optional database storage fails after output creation, the valid output artifact remains; the command returns failure and the artifact should be verified before recovery. Error messages do not echo malformed input.

## Evidence and remaining gates

Tests cover exact metrics, confidence 0/1 boundaries, all-failure batches, record-order stability, duplicate/invalid input, modified summaries, altered stored content, restart, authenticated creation, audit linkage, no escalation clearing, label separation, primary failures/abstention and command-line artifact preservation.

P06 remains open for a real Jev adapter/dataset, independently verified labels, adequate sample coverage, model-version/target binding, calibration fitting and validated review policy, protected evidence storage, deep fallback dispatch and independent review.

Rollback: preserve the SQLite database, stop the local app and revert this increment. Older code ignores calibration_reports. Existing calibration audit events remain valid in the preceding hash chain. No risk or execution-state migration is introduced.

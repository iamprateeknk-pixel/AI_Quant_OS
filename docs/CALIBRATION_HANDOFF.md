# Classifier evaluation handoff

## Produce and verify a report

Create an observations JSON file with provider_id, provider_version, dataset_ref and records. Each record requires sample_id, input_ref, label_ref, instrument_id, expected, predicted, confidence, outcome and latency_ms. The expected label is TRENDING, RANGING or HIGH_VOLATILITY. VALID requires a known prediction and confidence; ABSTAIN requires UNKNOWN and confidence; TIMEOUT/FAILED/INVALID/EXPIRED require null prediction and confidence.

```powershell
python -m ai_quant_os.evaluation_cli report observations.json --output calibration-report.json --database calibration.sqlite3
python -m ai_quant_os.evaluation_cli verify calibration-report.json
```

After package installation, ai-quant-evaluate exposes the same commands. Existing output artifacts are never overwritten. Reports contain labels and input references; keep them in the intended evidence environment.

## API

- POST /api/v1/paper/decisions/calibration uses the existing X-Operator-Token and accepts the same observations batch. The server assigns creation time and computes all metrics; clients cannot supply approval fields.
- GET /api/v1/calibration/{report_id} reads the verified artifact.
- AI_QUANT_PAPER_DB enables persistence. Without it, API reports are in memory and disappear at restart.

## Run a provider benchmark

Use ai_quant_os.classifier_benchmark.BenchmarkCase and run_benchmark with an injected FastClassifier, held-out cases, dataset_ref and an aware creation timestamp. Case labels are not sent to the provider. The replay clock is anchored at source snapshot time, so an archival adapter must emit timestamps in that logical timeline. The gateway enforces its normal evidence/schema/expiry rules during evaluation. Actual provider I/O must remain bounded and cancellation-cooperative.

Reports evaluate primary output. Gateway fallback predictions are not credited to a failed primary. Latency measures gateway invocation and validation; it is not a separate network-only measurement.

## Review before policy use

All current artifacts remain EVIDENCE_ONLY. The gateway continues to require escalation even if calibration_ref names a stored report. Review dataset provenance, independently verifiable labels, leakage/correlation, coverage across instruments/regimes, model-version binding, calibration fit and drift before installing any acceptance policy. No current report can authorize an order.

See ADR 0009 for metric definitions, storage limits and rollback. Production Jev benchmarking and the validated acceptance registry remain unfinished.

# ADR 0057: Descriptive A05 paper volatility and supplied L1 observations

Date: 2026-10-03

## Decision

Implement A05 0.2.0 as a partial shadow mission using the existing persisted
permission-checked graph. Keep deterministic log-return statistics in a pure
module, evidence verification in the source boundary, and the typed advisory
envelope in its mission adapter. Raw quote/bar/spread lineage must reproduce the
world snapshot. Contexts retain unavailable session and broker telemetry facts.

Add a separate authenticated paper L1 input, leaving the established quote
request and sealed market/risk/strategy source contracts unchanged. Supplied
sizes are synthetic base-unit observations bound to one exact quote. A stale
or replaced quote withholds current sizes. Imbalance measures displayed
quantities only and cannot establish executable liquidity.

Constrain uncalibrated confidence to zero and preserve escalation and independent
risk gates. The abnormality feature compares the latest return with preceding
returns, withholding irregular cadence or degenerate baselines. Use bounded
source contracts and decimal arithmetic independent of caller precision.

## Evidence and limits

Retain a bounded six-case synthetic recipe and graph replay report with source
hashes and a JSON Schema. Exercise the actual API, persistent receipts, source
tamper checks and deterministic order rejections. Full verification evidence is
recorded in the build status after the regression run finishes.

Actual broker/session feeds, independent calibration, continuous scheduling,
production execution/deployment, A05 repair/evolution integration, promotion and
all live-release gates remain open. Existing quote/size storage is process-local;
retained receipts are historical and never refresh live evidence.

## Rollback

Preserve the state directory and report/source bytes and revert this increment
with the local application stopped. No migration is added. Older code may not
read archived A05 0.2.0 receipts because its registry lacks the manifest. Restore
a compatible reader to access them. No order or safety transition is required.

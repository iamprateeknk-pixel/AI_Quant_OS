# ADR 0201: Publish source-bound daily paper execution attribution

Date: 2026-10-13

## Decision

Add a protected daily execution-attribution publication that depends on an already published daily accounting report. The server reads the current paper execution ledger, checks its opening history and day fills against the retained daily checkpoints, and binds selected fills to the closing order ownership and durable initial decision benchmarks. Retain the report and its selected sources in a separate additive table so existing daily checkpoint/report bytes remain readable.

Exact retries return the first publication. Conflicting source or report changes are refused. Missing initial benchmarks remain explicit gaps. The contract grants no candidate, risk, broker or live authority.

## Consequences

P09 can inspect daily execution cash-flow components alongside daily mark-to-market accounting. The opt-in daily scheduler publishes reports and retries temporary failures only after a current audit-integrity check; restart rebuilds its retry queue from immutable daily receipts. A protected range endpoint now rolls up retained daily rows by strategy/version/instrument with explicit missing days and benchmark gaps. Fill-time quotes, latency, financing, restatements and comparison against candidate shadow portfolios remain open. See `DAILY_EXECUTION_ATTRIBUTION_HANDOFF.md` and `docs/schemas/daily-execution-evidence.schema.json`.

# ADR 0007: Versioned advisory paper FX world state

## Contract

`GET /api/v1/world-state` returns schema 1.0 with a content-derived world_state_version, UTC as_of, valid_until, EMPTY/DEGRADED/READY status, instrument features, evidence references, currency coverage twins, source PAPER_SIMULATED and policy paper-regime-v1. The version fingerprints timestamp, policy settings, evidence and output. An identical snapshot at an identical time is reproducible. This advisory version does not replace the existing quote/intent world_state_version namespace or issue RiskDecision objects.

`POST /api/v1/paper/world-state/bars` requires the existing operator authorization and audit-integrity preflight. It accepts 1-1,000 canonical OHLCV bars per batch. The P02 Bar/BarDataset validation rejects invalid prices, naive timestamps and conflicting duplicates. Unknown instruments and future bars are rejected. All incoming histories are validated before the in-memory update. Retained identical records are idempotent; each pair retains at most 64 latest bars. The authenticated ingestion records its inserted count in the audit history.

## Features and policy

- Mid price: (bid + ask) / 2. Spread: (ask - bid) / mid * 10,000.
- Return: (last close / first close - 1) * 10,000 across the retained visible history.
- Step returns: (next close / preceding close - 1) * 10,000. Realized volatility is the population standard deviation of these step returns, using Decimal arithmetic with precision 28. It is not annualized or adjusted for irregular bar spacing.
- At least three bars and a last-bar age no greater than 60 seconds are required. Quotes must be trusted, not future-dated and at most five seconds old. A quote/history failure leaves regime UNKNOWN.
- Fresh evidence is HIGH_VOLATILITY at step volatility >= 25 bps, otherwise TRENDING at absolute window return >= 20 bps, otherwise RANGING. These fixed paper thresholds are uncalibrated; they carry no confidence score or order authority.
- Snapshot expiry is bounded by a five-second TTL and every currently fresh quote/history deadline. Future bars are excluded even when reading an earlier snapshot time after later ingestion.
- Currency twins describe related configured pairs and their fresh-quote count. Macro evidence is explicitly unavailable; currency strength and economic policy are not inferred from coverage.

## Evidence and limitations

Tests cover reproducibility, all quote failure states, stale/insufficient history, atomic conflict rejection, bounded retention, future rejection, duplicate ingestion, classification boundaries, no lookahead, source-bounded TTL and command authorization. API reads create no orders. Mission Control renders empty/degraded evidence and currency coverage; production build and lint are checked.

History and quotes are currently in memory and must be reloaded after restart. Ingestion and its audit append are separate transactions; an audit storage failure can leave advisory memory updated, and must be investigated. P02 archives remain the durable replay source. Full macro twins, durable publications, Jev benchmarking/adapter, calibrated confidence, fallback gateway, cross-service TTL enforcement and independent review remain P06 work. The existing kernel approval registry remains the only authority accepted by the paper gateway.

Rollback: stop the local application and revert this change; no database schema changes are introduced. Existing audit records for ingestion remain valid under the preceding audit implementation.

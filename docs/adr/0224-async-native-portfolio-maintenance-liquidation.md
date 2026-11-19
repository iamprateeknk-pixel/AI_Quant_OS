# ADR 0224: Asynchronous native portfolio maintenance liquidation

## Status

Accepted for recorded-quote PAPER diagnostics.

## Context

The asynchronous native FX portfolio shares one USD margin account and caps
fills to displayed source-side liquidity. Its schema 1.1 execution model had no
maintenance-margin controller, so a declining account could retain positions
without a deterministic closeout record.

## Decision

Schema 1.2 uses replay model `ASYNC_RECORDED_QUOTE_PORTFOLIO_USD_V3`. At each
unique recorded quote event, the controller marks each open leg at its adverse
executable quote and computes USD equity and 1x required margin from retained
fills and the latest quotes no older than the configured age limit. A breach
records its source timestamp, equity, required margin and affected instruments.
Each affected strategy is stopped and submits source-size-limited market closes
on subsequent quotes. No strategy decisions resume after a breach. A run whose
sources end before all positions close fails verification instead of exporting
a successful liquidation report.

Schema 1.1 and its original schema file remain unchanged for historical
evaluation evidence. Schema 1.2 has a separate additive schema file. If any
quote needed to value an open basket is stale, maintenance evaluation is deferred
until fresh evidence arrives. Risk-increasing orders remain blocked by the
existing collateral freshness gate; risk-reducing closes remain allowed.

## Consequences

This is an asynchronous recorded-quote model with top-of-book visible liquidity.
It does not model intratick movements, hidden depth, venue latency, exchange
acknowledgments, broker-specific margin tiers, financing, or live liquidation.
It remains PAPER research evidence with no promotion authority.

Rollback is a code revert. Existing schema 1.1 reports and their schema digest
remain valid and are not rewritten.

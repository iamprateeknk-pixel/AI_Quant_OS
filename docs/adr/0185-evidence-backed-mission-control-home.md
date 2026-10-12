# ADR 0185: Evidence-backed Mission Control homepage

- Status: accepted for local PAPER operation
- Date: 2026-10-13

## Context

The homepage showed an unavailable live-position metric and a disconnected-market placeholder while the application already exposed paper positions, portfolio valuation, order/fill records and a market snapshot. A separate protected BrokerHealth contract is available for profile observations.

## Decision

Compose the homepage from current backend reads in parallel. Show paper P&L only when the portfolio reports valuation available, count open positions from exact decimal strings without floating-point coercion, link fills to their paper order IDs, and label missing market/portfolio evidence as unavailable. Read broker health only through the server-owned operator session. Do not infer live execution, strategy allocation, agent health or reconciliation readiness.

## Consequences

The homepage presents more of the available operational evidence without manufacturing metrics. It still lacks a historical market chart and several release-gated widgets. Browser visual/accessibility acceptance and full P12 acceptance remain outstanding.

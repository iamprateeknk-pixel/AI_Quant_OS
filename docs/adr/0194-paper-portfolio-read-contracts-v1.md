# ADR 0194: Version paper portfolio read contracts

- Status: accepted for read-only PAPER portfolio APIs
- Date: 2026-10-13

## Context

The positions and currency portfolio APIs supply P&L, marks, balances, and exposure to Mission Control. Their unavailable valuation fields are significant safety evidence, but the complete response shapes were not published as closed schemas.

## Decision

Publish strict JSON Schema 2020-12 contracts for `GET /api/v1/portfolio/positions` and `GET /api/v1/portfolio/currencies`. Validate each actual API response, including null valuation/exposure values when marks or FX rates are unavailable. Keep PAPER mode and the four configured instrument relationships explicit.

## Consequences

Consumers can validate current portfolio responses without inferring zero for unknown valuation. The schemas do not add valuation sources or change P&L/risk authority. P00/P05/P12 acceptance remains open.

# ADR 0188: Same-snapshot context on Market Detail

- Status: accepted for read-only paper presentation
- Date: 2026-10-13

## Context

Market Detail showed only a validated paper quote, while the paper world-state read also exposes bar count, spread, return, realized volatility, and regime. Combining observations from different world-state versions could misrepresent current conditions.

## Decision

Show those context fields only when the quote is fresh, the world-state read is current, the instrument matches, and both reads carry the same world-state version. Validate numeric strings and supported regime values before projection. Otherwise withhold the metrics and identify their state as unavailable.

## Consequences

The page exposes more available paper evidence without calculating or upgrading its freshness. It does not imply a historical chart, calibrated regime, liquidity depth, or trading eligibility. P02, P12, browser review, and the remaining P00-P13 gates stay open.

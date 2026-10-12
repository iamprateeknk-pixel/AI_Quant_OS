# Market detail context handoff

## Summary

Market Detail now shows the simulated quote alongside same-snapshot paper context: bar count, spread, recent return, realized volatility, and the current paper regime. The page joins the quote and protected-by-contract world-state read using the exact world-state version and requested instrument. It withholds context if either read is unavailable, the quote or history is stale, the versions differ, or metric values are malformed.

## Verification

- Focused market-context tests cover matching snapshots and stale, missing, mismatched, and malformed observations.
- Full web regression: 169 tests passed.
- Next.js production build and TypeScript checks pass.
- Browser visual and accessibility acceptance remain open.

## Limits

- This summary is not a historical price chart or market-depth view; the available API does not provide a chart series.
- Paper regime thresholds are uncalibrated and do not imply strategy eligibility or trading authority.
- P02 and P12, and all other P00-P13 acceptance gates, remain in progress.

## Rollback

Revert the Market Detail context selector and page integration, its regression tests, and this handoff together. No backend or persistent state is changed.

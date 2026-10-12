# Mission Control homepage handoff

## Summary

The homepage now summarizes actual paper state from the control API: backend mode and safety state, marked portfolio P&L, gross worst-case exposure, and nonzero paper positions. Its market panel reads the current EURUSD snapshot and exposes validated freshness, source, observation time and world-state identity. Broker health uses the protected server-side operator session. Recent orders show their intent, risk, order, any recorded paper broker acknowledgement and linked fill-record references.

The page labels observations as paper data, exposes unavailable/unvalued states, and does not present strategy allocation, agent health, live positions, or broker reconciliation without corresponding evidence. The recent order table includes any recorded paper broker acknowledgement and joins fill counts by the backend order ID.

## Verification

- Exact decimal position count tests cover signed values, exponent notation, zeros and malformed inputs.
- Full web regression: 162 tests passed.
- Next.js production build and TypeScript checks passed.

## Limits

- The market panel is a single quote snapshot; no historical chart series or overlays are available from this read.
- Broker reconciliation remains unassessed by the current health provider.
- Browser visual and accessibility acceptance remain open. No live broker, provider credentials, or external service was used.
- P12 and all other P00-P13 acceptance gates remain in progress.

## Rollback

Revert the homepage reads/layout, exact position-count helper, tests and this handoff together. No backend state or stored data changes.

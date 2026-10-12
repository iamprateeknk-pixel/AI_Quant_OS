# Safety and risk UI read handoff

## Summary

Mission Control's system status and Risk Center reads now use strict projections aligned with the published API contracts. The browser rejects unknown fields/states, noninteger or negative order counts, wrong instrument identity, malformed numeric strings, negative ceilings/pending quantities, and invalid currencies. Risk quantities remain exact source strings; the expected instrument is EURUSD for the current Risk Center request.

## Verification

- Focused read-model tests pass for valid safety/risk values and invalid states, bounds, unknown fields, and instrument mismatch.
- Full web regression: 174 tests passed.
- Next.js production build and TypeScript checks pass.

## Limits

- The Risk Center still requests the API's default EURUSD exposure. Other instruments are supported by the schema but are not selectable in this UI flow.
- The parser validates response shape and numeric bounds; backend transactional risk evaluation remains authoritative.
- Browser visual/accessibility acceptance and P05/P12/full P00-P13 gates remain open.

## Rollback

Revert the risk read model, server API projection wiring, tests, and this handoff together. No backend or persistent state changes are involved.

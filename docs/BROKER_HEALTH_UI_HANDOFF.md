# Broker health Mission Control handoff

## Summary

Mission Control's Brokers section now reads the protected BrokerHealth v1.0 registry through the existing server-side operator session and displays the five fixed paper profiles. It preserves `UNKNOWN`, `UNASSESSED`, and missing measurements as such. Health scores are displayed only when the source reports a score with complete measured evidence. Native execution remains unreleased.

## Files

- `apps/web/src/features/brokers/health.ts`
- `apps/web/src/features/brokers/health-panel.tsx`
- `apps/web/src/features/operator-session/server.ts`
- `apps/web/src/app/[section]/page.tsx`
- `apps/web/tests/broker-health*.test.mjs`
- `docs/schemas/broker-health.schema.json`

## Verification

- Broker health parser, server-read and panel tests pass.
- Full web regression: 156 tests passed.
- Next.js production build and TypeScript checks passed.
- Browser visual acceptance was not performed in this environment.

## Limits and open dependencies

- The current backend derives connection state from the local router snapshot and leaves quote age, order latency, reject rate, reconciliation, spread and account state unassessed. The UI does not treat this as a connected-broker health measurement.
- No provider or broker credentials were used. No order, deployment or external action is enabled by this view.
- P04/P12 and all other P00-P13 acceptance gates remain open.

## Rollback

Revert this increment's page wiring, panel, parser, tests and this handoff together. No backend state or persisted data is changed.

# World State UI contract handoff

## Summary

The browser server read now validates the complete paper world-state response before projecting fields used by Market Detail. It enforces the exact v1.0 property sets, four configured FX instruments, aware timestamps and positive validity interval, world-state fingerprint shape, supported quote/history/regime enums, finite decimal strings, bar and evidence-reference bounds, currency coverage, fresh-quote counts, and status consistency. It drops internal currency labels, policy metadata, and evidence hashes from the UI projection. Expiry is calculated from the source `valid_until` and never refreshed by the parser.

## Verification

- Focused parser tests cover valid current and expired snapshots, unknown fields, naive timestamps, invalid time windows, duplicates, bad metrics/regimes, and inconsistent currency relationships.
- Full web regression: 172 tests passed.
- Next.js production build and TypeScript checks pass.

## Limits

- The UI parser mirrors the current fixed four-pair PAPER API contract. A deliberate API version or instrument-universe change must update this projection and its fixtures.
- The checks validate representation and internal coherence; they do not independently verify the backend's hash commitment or freshness source.
- Browser acceptance and full P02/P12/P00-P13 gates remain open.

## Rollback

Revert the World State projection and contract tests together. The backend response and stored snapshots are unchanged.

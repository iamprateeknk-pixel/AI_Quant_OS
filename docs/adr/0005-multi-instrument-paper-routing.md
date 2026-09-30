# ADR 0005: Multiple paper instruments with shared currency limits

## Decision

The API paper catalog contains EURUSD, GBPUSD, USDJPY and EURJPY. The catalog owns base and quote currencies and simulator symbol mapping. No live route is added.

Each instrument receives its own unit exposure snapshot and the existing 1,000 per-order / 1,500 position ceilings. The snapshot fingerprint covers the entire ledger, so a change to another instrument invalidates a stale approval. All instruments share the fixed USD-valued currency policy: 2,000 per currency and 4,000 gross. Approval, reservation and pending dispatch continue to enforce that policy transactionally.

Reconciliation compares net quantities, quote cash flows and exact fill records separately by instrument. Overall matching requires every instrument to match. Quantities and cash flows from different currencies are never summed into a position. Version 1.1 exposes an instruments list; legacy scalar fields remain the explicitly labelled EURUSD summary. Recovery checks this complete comparison under the gateway lock. Manual and simulator fill writes take the same lock.

Startup examines stored order states before applying display-only fill statuses. Complete fills cannot conceal an UNKNOWN or DISPATCHING order.

## Read contracts

- `/api/v1/instruments`: fixed paper metadata, schema 1.0.
- `/api/v1/portfolio/positions`: all configured instrument positions, schema 1.0.
- `/api/v1/portfolio/snapshot?instrument=GBPUSD`: selected position; EURUSD default retained.
- `/api/v1/risk?instrument=GBPUSD`: selected unit ledger and shared policy ceilings.
- Orders and fills expose instrument identifiers. Mission Control displays currencies on position rows and uses combined gross currency exposure in its overview.

## Evidence and limits

Tests cover independent unit reservations, shared USD and EUR limits, concurrent retries, missing/stale cross conversions, mixed fill reconciliation and uncertain fully-filled startup. Browser checks use an isolated in-memory fixture with simulated fills for all four instruments. Production broker fills, account equity/margin controls, operator sessions and independent release review remain open gates.

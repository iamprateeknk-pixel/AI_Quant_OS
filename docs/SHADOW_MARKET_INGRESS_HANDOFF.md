# PAPER market-bar retention for shadow ingress

The authenticated `POST /api/v1/paper/world-state/bars` route now stores every accepted bar in the same persistent SQLite workspace as the PAPER audit chain before updating the in-memory world-state history. Identical repeated bars are idempotent. A different bar for the same instrument and timestamp is rejected. Bar rows carry canonical JSON and a content hash; reads verify both before returning data.

`WorldStateStore.read_bars(instrument_id, start, through, limit=4096)` reads an inclusive UTC range and refuses results beyond the requested bound. Batch writes have a configurable internal capacity (one million rows by default); capacity is checked once per batch, and a failed write or audit append rolls back the entire batch. The audit event and bar rows share the same SQLite transaction and lock. If durable storage or audit verification fails, the API returns unavailable and does not update current in-memory market freshness.

This is restart-safe local retention for bars explicitly submitted to the PAPER route. It does not attest who generated the prices, prove complete market coverage, verify external provider signatures, or make a candidate shadow job run automatically. Row hashes detect altered content when read, while no deletion-proof archive head is provided. The data remains PAPER_SIMULATED and all broker, order, and promotion authority is unchanged.

Verification covers restart replay, duplicate retries, conflicting updates, capacity rollback, row tampering, audit append failure, audit-chain corruption, and API storage failure. Focused world-state and market-ingress tests are in `tests/test_world_state_store.py`, `tests/test_world_state.py`, and `tests/test_paper_market_ingress.py`.

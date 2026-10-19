# ADR 0212: Asynchronous tick portfolio visible liquidity slices

## Status

Accepted for bounded recorded-quote PAPER research; venue execution behavior is not modeled.

## Decision

The asynchronous recorded-tick portfolio limits each market child order to the
remaining displayed size on the execution side of its source quote. A requested
target that is only partly filled remains an intent and is reconsidered on later
quotes. The synthetic PRICE and TICK events for one source row share one bid/ask
size budget. Fees apply to each submitted child order.

The report advances to schema 1.1 and replay model V2, and declares
`SOURCE_VISIBLE_SIDE_ORDER_SLICES_V1`. Independent reconciliation checks
integral positive quantities, target-position bounds, source prices and
cumulative per-quote side capacity. The existing single-instrument tick replay
retains its complete-reversal liquidity gate.

## Recovery and limits

This uses recorded top-of-book size only. It does not model deeper book levels,
queue priority, measured network or venue latency, exchange partial-fill
acknowledgements, maintenance margin, or liquidation. A report can end below a
strategy's target when the source ends or later quotes lack displayed size.
Results remain PAPER research evidence with no order authority.

Rollback is a code revert; there is no persistent state migration. Reports
exported under schema 1.1 retain their declared model and must not be relabeled
as V1 or interpreted as exchange execution evidence.

## Verification

Focused tests cover partial child-order sizing, cumulative side-capacity
tampering, per-order fee accounting, report verification, CLI export and the
existing strict single-instrument tick path. The current focused result is
recorded in `docs/BUILD_STATUS.md`; full-suite and independent review evidence
will be recorded when available.

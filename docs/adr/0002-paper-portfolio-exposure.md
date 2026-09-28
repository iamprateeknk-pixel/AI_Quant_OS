# ADR 0002 Paper portfolio exposure gate

Status: Accepted for the EURUSD paper simulator.

The per-order ceiling alone allowed individually valid orders to accumulate beyond a portfolio ceiling. The paper risk kernel now evaluates a snapshot of filled net EURUSD base units and unfilled buy/sell quantities. Its worst case quantity is the larger absolute endpoint of the interval from `filled - pending sells` to `filled + pending buys`.

The risk decision carries a fingerprint of the orders and fills used for that snapshot. Before the simulator receives a new order, the SQLite transaction checks that the fingerprint still matches and that the new worst case quantity obeys the fixed 1,500 unit ceiling. The transaction returns an existing receipt for a repeated request key before checking the fingerprint. A stale new request fails and requires a new risk evaluation. An order that cannot increase an existing overage may proceed in NORMAL safety state; other safety states still block new orders.

This gate covers one paper instrument in base units. It does not implement USD notional conversion, multi-currency aggregation, margin, broker positions, or a live outbox. A broker connection requires those controls and reconciliation before release. Rollback to the preceding commit requires preserving the SQLite file; this change adds no database column.

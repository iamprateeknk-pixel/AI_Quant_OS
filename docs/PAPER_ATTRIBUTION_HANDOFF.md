# Paper strategy contribution evidence

New orders can also retain initial decision-price evidence for a separate cash-flow decomposition. See [execution attribution handoff](EXECUTION_ATTRIBUTION_HANDOFF.md). Historical accounting snapshots keep their explicit benchmark gaps.

P09 is in progress. The first accounting path groups actual persisted paper fills by their original strategy ID/version and instrument. It produces cumulative cash, fee, inventory and marked-P&L contributions from an empty opening inventory.

## Read the report

`GET /api/v1/paper/evidence/strategy-contributions` requires the existing `X-Operator-Token` and valid paper audit integrity. It uses the configured paper database or current in-memory simulator ledger. It does not accept client-supplied fills, strategy labels, prices or approval claims in the request.

The order store reads original intent/order records and fills in one database transaction. The gateway verifies stored order/intent identities, command identity, risk identity, instrument, side, quantity, expiry and the literal PAPER route. Original record hashes bind ownership to that snapshot. The pure accounting function checks unique order/fill identities, accepted ownership, cumulative order quantity and fill times. Reads are bounded to 10,000 orders and fills, with at most 16 instruments/quotes. Invalid ownership or non-reconciling accounting prevents a successful report.

The response includes a UTC observation time, stable ledger fingerprint, strategy rows, per-instrument totals, marking method and evidence gaps. Decimal values remain exact JSON strings, including scientific notation supported by the published schemas. Storage/lineage errors return a controlled 503. No new order, broker, risk or agent permission is granted.

## Accounting

For each strategy/instrument:

- Signed quantity is buys minus sells.
- Cash before fees is sale proceeds minus purchase cash.
- Fee cash flow is negative actual recorded fees.
- Cash flow is those two components combined.
- Marked inventory is signed quantity times the common instrument mark.
- Marked P&L is cash flow plus marked inventory.

Every strategy uses the same instrument mark selected from the portfolio's net position: bid for a nonnegative net position, ask for a negative net position. This makes strategy contributions add to the existing portfolio liquidation-mark convention, including offsetting strategy positions. Independent bid/ask marks for individual strategies would instead charge the spread on internal offsetting inventory and fail this reconciliation.

Marks must be trusted, nonfuture and within the existing five-second quote age limit. Missing, stale, future or untrusted prices withhold marked inventory/P&L while preserving quantities, cash and fees. The `reconciled` flag means available accounting components agree with instrument totals; it does not assert that missing marks are known. Different quote currencies remain separate instrument totals; no USD/JPY summation or undocumented conversion occurs. Arithmetic uses a fresh Decimal context independent of the caller and refuses an inexactly reconciling result.

## Evidence and remaining work

The committed synthetic evaluation contains original ownership descriptors, two actual persisted manual paper fills, quotes, currency metadata and the resulting report. It replays from that retained source snapshot and validates with the evaluation and report schemas. The illustrative EURUSD strategies contribute 1.9 and -0.6 quote units, totaling 1.3 with fees included. These are accounting examples, not strategy or broker performance evidence.

The report explicitly lists absent decision-price benchmarks, agent-credit evidence, causal factor attribution and candidate-genome bindings. Original paper intents retain strategy identity but do not identify a resolved parameterized candidate genome. Registration of a research challenger therefore does not turn these fills into candidate shadow-validation evidence.

This report is cumulative, not a daily return calculation. Daily opening marks/inventory and durable accounting publication are implemented separately in the daily-evidence workflow; linking them to this report remains. Shadow portfolios, live-versus-shadow comparison, execution attribution, agent contribution and isolated counterfactual datasets remain. Holdout-to-forward shadow demotion is implemented as a local candidate diagnostic; PAPER-versus-shadow reality-gap alerts remain. The paper marking convention is not an executable guarantee of liquidity or a live-capital release gate.

## Compatibility

No database migration or existing order/fill contract change is required. The added snapshot query and read API are additive. Persist original paper records for lineage; quotes remain process-local and must be republished after restart, so a restarted report retains the ledger fingerprint but withholds current marked P&L until fresh quotes arrive.

Artifacts:

- `docs/evaluations/paper-attribution-baseline.json`
- `docs/schemas/paper-attribution-evaluation.schema.json`
- `docs/schemas/paper-strategy-contributions.schema.json`

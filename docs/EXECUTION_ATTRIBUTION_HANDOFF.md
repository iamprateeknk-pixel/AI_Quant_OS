# Initial-decision execution accounting

P09 remains in progress. Newly reserved paper orders now retain the exact trusted quote used for their initial Risk Kernel approval. The benchmark commits atomically with the order before any broker dispatch and remains immutable through acknowledgement, retries and pending recovery.

## Capture and compatibility

The Risk Kernel holds the approval's frozen market snapshot. The execution gateway can read it only for that issued, unconsumed approval; callers cannot supply a substitute quote or forged decision. Successful reservation writes `paper_price_benchmarks` with a canonical record/checksum and sets `paper_orders.benchmark_required=1` in the same transaction. Benchmark storage failure rolls back the order and prevents dispatch. The snapshot is removed from the in-memory kernel map when its approval is consumed or paper safety recovery clears approvals.

The additive migration initializes existing orders with `benchmark_required=0`. Their initial quote is unknown and is never reconstructed from a later price. Legacy pending recovery does not fabricate an initial benchmark. Pending recovery of a newly captured order retains its original approval/quote, so the measured initial-decision difference includes recovery delay and price movement.

Evidence reads retrieve ownership, fills and benchmark inventory in one bounded database snapshot. They verify record fingerprints, canonical encoding, order/intent/risk/instrument/world-state binding and valid requirement markers. Required-row deletion, unexpected legacy-row insertion, orphaned records and invalid metadata prevent a successful read. These local checks detect corruption/inconsistency; they do not authenticate records against a host administrator capable of replacing records and hashes.

## Report

`GET /api/v1/paper/evidence/execution-contributions` requires the existing operator token and valid paper audit integrity. It returns exact Decimal strings and grouped strategy/version/instrument contributions with separate quote-currency totals. No current quote is required, so the same source snapshot and observation time reproduce the report after restart.

The initial bid/ask provides a midpoint and a side reference: ask for a buy, bid for a sell. Using each actual fill's signed quantity:

| Component | Cash-flow convention |
| --- | --- |
| Reference midpoint cash | Negative signed quantity times initial midpoint. |
| Spread adjustment | Negative signed quantity times side-reference minus midpoint. |
| Decision-to-fill adjustment | Negative signed quantity times fill-price minus side-reference. |
| Fees | Negative actual recorded fees. |
| Actual cash | Sum of the four components. |

Negative adjustments reduce cash; positive decision-to-fill adjustments improve cash relative to the side quote. This is an accounting decomposition at actual filled quantities. It does not simulate different fill quantities, missed orders or an alternative strategy. The decision-to-fill component combines market movement, scheduling/recovery delay and execution differences. Fill-time market snapshots and latency evidence are still required to separate those causes.

Fill lineage, partial quantities, ownership and timestamps use the existing paper accounting validation. A benchmark must belong to the order and be known before its fill. Fresh local Decimal contexts trap inexact arithmetic; an unrepresentable calculation is refused instead of reporting silently rounded reconciliation. Both attribution APIs return a controlled verification failure when arithmetic cannot be represented exactly within the declared 28-digit context.

Coverage is `COMPLETE`, `PARTIAL`, `NONE` or `EMPTY`. Missing legacy references preserve actual cash/fees and counts, while withholding benchmark components for affected strategy rows and any incomplete instrument total. Empty or partial coverage cannot be treated as validation approval. The report lists absent fill-time/latency evidence and identifies manual-paper fill evidence when applicable.

## Evidence and remaining P09 work

The committed synthetic paper-broker example stores two initial kernel quotes before dispatch, records simulator fills and imports them through the existing idempotent broker-fill path. Its midpoint, spread, decision-to-fill and fee components reconcile to -7.02 EURUSD quote units of cash. Retained ownership/fills/benchmarks/catalog replay the report and validate with the published schemas. This is simulator accounting evidence, not real-broker performance.

Daily paper opening inventory/marks and immutable accounting reports are available through the dedicated daily-evidence workflow. A separate immutable daily execution report now embeds the existing execution decomposition for fills in that UTC day and binds it to the published daily checkpoints; see DAILY_EXECUTION_ATTRIBUTION_HANDOFF.md. Automated publication, resolved candidate bindings, shadow portfolio comparison, agent/causal contribution, isolated counterfactual datasets, reality-gap alerts/demotion and real-broker validation remain. No candidate or live-capital authority is added.

## Rollback and retention

Preserve the database and its WAL. Older code can ignore the added table/column, but orders created during a rollback may legitimately lack benchmarks and remain unmeasured after upgrade. Do not backfill those orders or downgrade requirement markers to hide missing evidence. Capacity remains 10,000 orders/fills/benchmarks per snapshot and 64 KiB per benchmark record.

Artifacts: `docs/evaluations/execution-attribution-baseline.json`, `docs/schemas/execution-attribution-evaluation.schema.json`, and `docs/schemas/paper-execution-contributions.schema.json`.

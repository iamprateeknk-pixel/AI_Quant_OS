# UTC daily paper evidence

P09 now supports persisted paper ledger/quote checkpoints and immutable daily accounting reports. Daily PnL is the day's cash flow including fees, plus closing inventory value, minus opening inventory value. This removes the assumption that every report starts with empty inventory.

## Accounting and price evidence

The UTC day is the half-open interval `[00:00:00, next 00:00:00)`. A fill exactly at opening belongs to that day; a fill exactly at closing belongs to the next day. Opening positions come from fills strictly before opening, closing positions from fills strictly before closing. Source checkpoints retain all captured fills, so their ledger hashes identify the full capture, including any post-boundary records excluded from the day's arithmetic.

Strategy/version contributions reconcile to each instrument's opening/closing quantity, daily cash, fees and available inventory/PnL. Quote currencies remain separate: USD and JPY values are never added. A fresh 28-digit Decimal context rejects unintended inexact arithmetic. Funding, deposits, withdrawals, broker balance reconciliation, financing/swap charges and cash FX conversion are not modeled by this paper ledger.

Each instrument uses one common portfolio liquidation mark at each boundary: bid for a nonnegative net position, ask for a net short. Every strategy uses that same price so offsetting positions reconcile. A side change can change the mark basis; this is accounting, not causal signal/execution attribution.

Boundary prices must be trusted, observed no later than the boundary and at most five seconds old. The checkpoint must be captured no earlier than the boundary and no later than five seconds afterward for its price to qualify. Late captures, missing/stale/untrusted prices and prices from after the boundary have explicit states; marked values are withheld. A flat strategy's inventory is exactly zero without a quote. If offsetting strategies still have inventory and prices are missing, their instrument's marked totals are also withheld.

`COMPLETE_ACCOUNTING` describes these available accounting values only. Agent credit, candidate genome binding, live/shadow comparison and causal attribution remain explicit evidence gaps. `INCOMPLETE_MARKS` retains cash, fees and quantities with nullable inventory/PnL. Neither state grants strategy promotion, risk approval or broker authority.

## Capturing and publishing

All routes require the configured operator token and valid existing paper audit integrity. Configure `AI_QUANT_PAPER_DB`; an in-memory application returns 503 because durable evidence capture is disabled.

| Route | Input / purpose |
| --- | --- |
| `POST /api/v1/paper/evidence/checkpoints` | `{ "checkpoint_id": "open-2026-01-02" }`. Capture server-owned ownership/fills, current quote snapshots, instrument catalog and UTC clock. |
| `POST /api/v1/paper/evidence/daily` | `{ "day": "2026-01-02", "opening_checkpoint_id": "open-2026-01-02", "closing_checkpoint_id": "close-2026-01-02" }`. Publish the closed UTC window. |
| `GET /api/v1/paper/evidence/daily/2026-01-02` | Retrieve the checked source-retaining daily artifact. |

Clients cannot submit reports, marks, ledger records, capture timestamps or filesystem paths. Capture an opening checkpoint at the day boundary and a closing checkpoint at the next boundary; use quotes observed at or just before the respective boundaries. Historical opening prices cannot be reconstructed from fills or today's quote. A late opening checkpoint can support incomplete cash/quantity accounting but does not invent a boundary price.

Matching checkpoint retries return the original capture even if the current book or quotes changed. Matching daily publication retries preserve the first publication time. Different source checkpoints/report content for an already published UTC day are refused. Unknown checkpoints/reports return 404, invalid window/lineage/publication conflicts return 409, extra fields return 422 and unavailable/corrupted reads return 503.

Opening historical fills must exactly match the closing capture's fills before opening, including identity, price, quantity, fee and source. Original ownership hashes and strategy/version must also match. Late imported pre-opening fills or changed historical ownership invalidate publication; the original checkpoint is preserved for investigation. Publication needs both stored source checkpoints and cannot silently replace them. Later imports after publication do not amend the report; revision/restatement workflows remain unfinished.

## Persistence and trust limits

The sibling database `<paper DB stem>.evidence.sqlite3` stores canonical typed checkpoints and reports with SHA-256 fingerprints. Source references are checked on reads and publication, and complete reports rederive from retained source values. Immediate transactions serialize immutable capture/publication conflicts. Limits are 1,000 checkpoints, 730 daily reports, 16 MiB per artifact and 64 MiB aggregate bytes; source duplication can exhaust capacity earlier. No automatic deletion occurs. Preserve the database/WAL during investigation or rollback.

Order/intent/fill ownership comes from the existing atomic gateway ledger snapshot. Quotes remain process-local snapshots and are not read in the same SQLite transaction as fills. If the ledger contains an event after the captured clock, capture fails; invalid quote timing is retained as an explicit mark gap. Operator-supplied paper quotes and manual paper fills are trusted local inputs, not independently verified market or broker evidence. After restart, new captures need fresh quotes; saved daily artifacts remain readable without them. Host administrators can replace data and hashes, and independent attestation remains open.

## Verified evidence and remaining work

The saved `docs/evaluations/daily-paper-baseline.json` was produced through the actual protected paper API with two simulator-accepted orders and two manual paper fills. The opening 10-unit EURUSD position is marked at 1.10 for 11 USD; the day sells 4 units at 1.20 with 0.20 fee, creating 4.60 USD cash; closing 6 units at 1.30 is 7.80 USD. Daily PnL is 1.40 USD, split as alpha +2.00 and beta -0.60. The earlier 0.10 fee is excluded from the current day's cash flow. This is a synthetic paper fixture, not actual broker performance.

The original stored source snapshots republished identically in an independent archive and survived restart. Typed/canonical replay and both checkpoint/daily JSON Schemas validated. Tests exercise boundary timing, mark refusal, day-edge fills, late history changes, currency separation, isolated/inexact Decimal arithmetic, immutable archive/restart/corruption, server-owned API inputs, protected retrieval and disabled persistence.

Automatic boundary capture/publication, reliable market ingress, daily execution and shadow aggregation, candidate paper assignments, agent contribution evidence, financing and ledger restatements remain unfinished. P09 comparison, reality-gap alerts/demotion and release reviews remain open. No paid provider or live broker was exercised.

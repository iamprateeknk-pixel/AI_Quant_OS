# Retained demo account observations — increment 0112

## Implemented

`SyncProjectionStore` imports an existing, verified account-sync gateway receipt.
The read-only gateway loader cannot reserve an attempt or dispatch native work.
A separate SQLite journal and audit chain commit the receipt and its audit event
in one transaction. Every read verifies the chain and projection agreement.
Corrupt, unavailable or pre-existing empty stores are withheld without reset.

The first receipt pins connection policy and account scope. Later captures must
have strictly increasing capture timestamps and history windows that overlap or
touch the retained coverage interval. Disconnected forward and backward windows
are rejected atomically. Reported interval continuity does not prove complete
native history. There is no autonomous cursor, polling or reconnect loop.

Stable deal references retain one identity across windows. Changed normalized
content adds a revision with the source receipt and capture timestamp. A native
canceled trade observation can change from FILL to CASH while retaining its older
fill version. This records observations; it does not attest that a broker issued
a correction, or reverse an entry in a portfolio or cash ledger.

Previously observed deals absent from a later window covering their timestamp
are flagged as missing. Earlier versions remain available. Reappearance removes
the missing flag. Positions, active orders and account facts come from the latest
batch; missing orders are not interpreted as fills or cancellations.

Repeated imports compare the original finished receipt commitment, including
when its quotes have expired. A matching UUID from a different journal cannot
alias an imported receipt. The view explicitly labels quote context FRESH or
STALE; account freshness remains UNVERIFIED and reads remain non-atomic.

## Bounds and use

Use a dedicated new projection database, an enrolled `DemoSyncRequest`, and an
already finished `DemoObservationGateway` attempt. Call `apply`, then `view` or
`deal_versions` with the same connection policy. Returned models are exported in
`demo-sync-projection.schema.json`. Scope violations and unavailable reads raise
fixed `ProjectionRejected` codes; imports return typed withheld results.

Limits are 1,000 receipts, 4,096 total deal versions, 128 revisions per deal,
128 missing references and 32 KiB per record. Capacity withholds the whole import;
there is no truncation, eviction or reset. This bounded implementation verifies
and folds retained receipts on every operation. Larger deployments need an
explicit checkpoint/archive design and migration review.

## Verification and remaining acceptance

Focused fixture checks cover overlap/revision identity, missing records,
reclassification, restart, receipt conflict, quote expiry, history gaps, foreign
scope, equal capture timestamps, capacity, unavailable reads and atomic rollback.
Existing sync, gateway, worker, observation and paper router checks also run.
No real SDK, terminal, credentials, broker or live capital was used.

The component is a library boundary and is not composed into HTTP or paper
routing. Native demo acceptance, continuous synchronization, reconnect, orphan
worker custody, native history completeness, full order lifecycle reconciliation,
external order idempotency, alternate brokers, eligibility and independent
critical review remain pending. Full-system and P13 acceptance are not claimed.

## Rollback

This adds a separate database and public schema; existing gateway tables and
schemas stay compatible. Retain projection databases and audit history on
rollback. Earlier code cannot consume projection files. Do not replace, erase or
reuse any unresolved gateway journal or terminal custody record.

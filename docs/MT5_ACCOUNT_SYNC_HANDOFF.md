# Demo account synchronization — increment 0111

## Implemented read path

The fixed worker accepts a new `CAPTURE_DEMO_ACCOUNT` operation with the existing
explicit demo connection enrollment and a declared UTC history interval. The
same supervisor owns the child, bounds input/output and execution, and retains
uncertain custody. The same durable gateway reserves the attempt before dispatch
and reuses completed receipts without another SDK call. Quote-only requests,
receipt serialization and their exported schema remain compatible.

The sync policy initializes the declared account, verifies exact demo identity,
reads account balances and mapped symbol facts, reads all open positions and
active orders, reads deals for the declared history interval, then verifies
account identity again. It never submits, checks, modifies or cancels an order.
The API and paper broker router do not compose this path.

## Normalized facts

- Account currency, balance, credit, equity, reported profit, margin/free margin,
  leverage and native netting/exchange/hedging mode.
- Individual positions retain broker ticket and position identifier, mapped
  instrument, side, lots, current-symbol quantity units, prices, profit/swap and
  native creation/update timestamps. Hedged positions remain distinct.
- Active orders retain ticket, explicit order kind/state, initial and remaining
  lots, open/stop-limit prices and setup timestamp. Unsupported close-by types or
  invalid active-order states withhold the batch.
- BUY/SELL deals become execution observations with native order/position links,
  entry kind, lots, price and signed reported profit/commission/swap/fee. Monetary
  fields carry the declared account currency. They are not converted into
  `PaperFill`, whose fee/source and portfolio assumptions differ.
- Nontrading deal types, including balance/credit, commission, tax and canceled
  BUY/SELL correction records, remain separate cash/correction observations.
  They cannot become execution fills or fabricated zero-price trades.

Deal references bind the enrollment and declared account identity fingerprint to
the native ticket, so overlapping windows retain identity without using comments,
names, external identifiers or raw account credentials. A deal correction can
change the public content for that identity; consumers must handle revisions
rather than blindly discarding later versions. Fingerprints are not anonymity or
native attestation. Financial fields are reported observations, not a reconstructed
cash ledger, realized-performance certificate or settlement proof.

## Scope and consistency

History is an explicit positive UTC interval of at most one day, with its end no
later than capture start. Returned deals must lie inside that inclusive interval.
The policy distinguishes empty tuples from None/error responses. It never silently
filters unknown symbols, truncates collections or deduplicates conflicting native
records. Limits are eight positions, eight active orders and sixteen total deals.
Oversize collections or a public sync report over 12 KiB withhold the entire batch.
The existing 16 KiB durable-result and 64 KiB worker-output limits remain unchanged.
Native SDK allocation before a response is not an attested memory limit.

Reads are sequential and cannot prove an atomic server snapshot. Every batch is
labeled NON_ATOMIC_SEQUENTIAL_READ and account freshness UNVERIFIED. The fresh
quote deadline bounds receipt reuse; it does not attest the freshness of terminal
account cache. Position/update and order timestamps may be older than capture.
No account equity, fill sum or active-order volume equality is invented across
the read interval. Quantity conversion uses current symbol contract size; history
marks that size UNVERIFIED_AT_EXECUTION. Signed fees and rebates retain their
reported signs. None of these facts grants portfolio or order authority.

## Durable compatibility and verification

The sync operation has separate typed report, supervisor result and gateway
receipt schemas. Its request fingerprint includes the operation, connection
identity and history interval. Changing the interval under the same attempt is a
conflict. Completed sync receipts replay through the existing journal; expiry
withholds data without refresh. An unfinished sync blocks quote and sync requests
through that journal. Restart does not let a new owner reconcile a lost child.
Existing exact-child reconciliation rules apply to the sync profile.

Verification covers fixture financial/position/order/deal normalization, empty
versus error, unknown symbols, unsupported enums, duplicates/capacity, signed
fees, overlap identity, window bounds, account switching, cleanup failure, forged
scope/quantity/event identities, fixed worker IPC, a real missing-terminal worker,
journal replay/expiry/conflicts/uncertainty and both schema contracts. All previous
observation, worker, journal and paper routing checks are included. No real terminal,
native SDK or broker credentials were used; fixture success is not native acceptance.

P04 still requires actual Exness demo acceptance, durable orphan custody evidence,
reconnect/continuous synchronization, incremental cursor/correction handling,
broker order idempotency and routing, account eligibility/capability composition,
alternative broker adapters and independent critical review. All P13 live gates
remain open. There is no existing-store migration or dependency lock change.
Rollback must retain unresolved journals and their compatible supervisor; old
code cannot interpret a stored ACCOUNT_SYNC result and must not reset that store.

## Vendor contracts

The native read/None-error semantics follow [positions_get](https://www.mql5.com/en/docs/python_metatrader5/mt5positionsget_py),
[orders_get](https://www.mql5.com/en/docs/python_metatrader5/mt5ordersget_py) and
[history_deals_get](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py).
Mappings follow the native [deal](https://www.mql5.com/en/docs/constants/tradingconstants/dealproperties),
[order](https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties) and
[position](https://www.mql5.com/en/docs/constants/tradingconstants/positionproperties) definitions.

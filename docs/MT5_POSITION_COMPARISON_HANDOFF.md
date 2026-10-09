# Declared opening position comparison — increment 0125

The demo sync service offers an offline comparison between retained normalized
fill movements and the latest observed positions. It requires an explicit
opening-position declaration. It never invents a zero opening, imports fills
into the paper portfolio, repairs a broker position, reconciles workers, starts
a timer/capture, or updates a heartbeat.

## Declaration and source

`DeclaredOpeningPositions` carries the original account scope hash, exact retained
history start, and signed opening lots by instrument and position identifier.
The boundary is IMMEDIATELY_BEFORE_HISTORY_START; fills at the start are included
after that opening state. The declaration must enumerate all nonzero opening
positions. An explicitly supplied empty tuple asserts a flat opening. This is an
operator declaration with DECLARED_UNVERIFIED attestation, not independently
proved native history. Obtain scope/start from the original normalized capture
or verified projection; this command does not infer the opening inventory.

The opening schema is `demo-opening-positions.schema.json`. Each identifier must
be unique and positive; lots are finite, signed, bounded to one billion in
absolute value and at most eight decimal places. Inventory is capped at 128.
The CLI accepts a bounded 32 KiB JSON declaration and rejects duplicate fields,
extra fields and malformed input before opening state.

```text
python -m ai_quant_os.mt5_sync_service --config <original-config.json> --state-dir <existing-state> --compare-position-history <declared-opening.json>
```

This is mutually exclusive with inspection/recovery actions. Mutation revision
or window parameters retain their existing refusal. Original private connection
configuration remains necessary to bind the projection; a noncredential
placeholder allows offline reading without a password or native Windows launch
flag. Actual capture/recovery still use their original password/platform gates.
All three journals must already exist; CLI ownership and incomplete-checkpoint
guards still apply. Stop the normal service before running the offline command.

## Comparison

`SyncProjectionStore.position_history()` reads the complete audited projection
once under its lock and SQLite transaction. It returns the latest positions and
latest version of every retained deal from that same logical snapshot. The
existing projection view uses the shared builder with its original wire shape.

Each latest FILL adds BUY lots or subtracts SELL lots for its stable position
identifier and instrument. Partial closes, INOUT reversals and hedged positions
therefore retain separate signed inventories; entry labels do not convert a
sell close into a buy movement. Corrected versions replace the recorded movement
for their identity exactly once. A latest cancelled deal represented as CASH
no longer contributes fill lots, and is counted separately. Other cash events
do not alter lot inventories. No instrument-net aggregation hides opposite
hedged positions.

For each opening/fill/current-position identity, expected lots equal declared
opening plus recorded signed movement. Difference is observed minus expected.
An absent identity in the complete current snapshot has zero observed lots; an
absent opening entry has zero only under the explicitly supplied declaration.
Rows are sorted by instrument/identifier and capped at 128 without truncation.
All per-source lots use the declared numeric bounds. A private 64-digit Decimal
context protects arithmetic from caller precision; result validators enforce
finite bounded eight-place arithmetic and consistency between rows and state.

CONSISTENT_WITH_DECLARATION means every lot difference is zero. DIVERGENT retains
differences. WITHHELD includes no comparison rows and a fixed reason: missing
prior deals, wrong opening scope/start, foreign/invalid source, missing history,
unmapped/conflicting position identity, wrong latest history endpoint, arithmetic
or row limits, or unavailable storage. Missing retained deals are never erased
or silently treated as cancellations. Latest window end must equal the complete
retained coverage endpoint; a backwards-only latest window is not used as a
current inventory comparison.

The report binds the declaration hash and latest source receipt, account scope,
history bounds and observed position time. Revised/cancelled deal counts remain
descriptive. The exported comparison schema fixes trading and portfolio authority
false, native/history attestation UNVERIFIED and consistency
NON_ATOMIC_SEQUENTIAL_READ. Exit 0 means consistent with the declaration; exit 2
means divergent/withheld or a sanitized command refusal.

## Limits and acceptance

This compares saved observations, including expired snapshots; it asserts no
current account freshness. The original sequential native reads are non-atomic,
and supplied history completeness/opening positions are unverified. Numeric
agreement does not prove deal lifecycle validity, execution prices, balances,
historical contract units, financing, cash reconciliation, eligibility or safety
for trading. No orders or paper-domain values are created from the report.

Tests cover declared flat/nonflat opening, real normalized partial close and
reversal fixtures, separate hedges, revised and cancelled deals, missing-history
refusal, malformed declarations, schema authority/arithmetic denial, numeric
bounds, caller precision, closed/foreign stores, unchanged heartbeat and actual
offline CLI with unchanged gateway attempt count. No actual SDK, terminal,
credentials or broker is used. Existing schemas and journal formats remain
unchanged; two wire schemas and the snapshot reader are additive. Rollback retains
all journals and declaration/report artifacts; removing the command changes no
audit history. Native completeness, cash accounting, portfolio integration,
custody resolution, installed deployment, independent review and P13 acceptance
remain pending. Focused checks do not establish full-system completion.

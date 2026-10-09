# Declared account-money comparison — increment 0126

The offline demo sync service can compare reported deal-money movements with
saved account balance and credit under an explicit opening and component
allocation declaration. It performs no native capture, account repair, worker
reconciliation, portfolio update, order submission, timer or heartbeat write.

## Declared opening and allocation

`DeclaredOpeningMoney` binds the original account scope, exact retained history
start, account currency, opening balance and opening credit immediately before
the start. It requires explicit allocations by observed deal kind: each of
profit, commission, swap and fee must name BALANCE or CREDIT. There are no
implicit rules, guessed credit/balance classifications or sign inversions.
All required source kinds must be classified before arithmetic is reported.
Repeated kind rules are rejected. Unused explicit rules can remain in the
declaration and its hash.

Opening balance and components are signed; opening credit is nonnegative.
Source amounts are finite, at most one trillion in absolute value with up to
eight decimal places. Rules are bounded to the existing 18 normalized MT5 kinds.
The CLI caps declaration JSON at 32 KiB and rejects duplicate/extra fields and
malformed data before state access. The exported opening schema is
`demo-opening-money.schema.json`. Obtain original scope/history bounds from the
normalized capture or verified projection; no opening state is inferred from
an arbitrary history window. Opening and allocations remain DECLARED_UNVERIFIED.

```text
python -m ai_quant_os.mt5_sync_service --config <original-config.json> --state-dir <existing-state> --compare-money-history <declared-opening-money.json>
```

This action is mutually exclusive with position comparison, inspection and
recovery. Existing revision/window mutation arguments retain their refusal.
The original private connection configuration is required to bind the retained
projection. A noncredential placeholder permits offline use without a password
or native Windows launch flag; actual capture/recovery keep their original
enrollment, password, platform and launch requirements. Existing three-journal,
CLI ownership and incomplete-checkpoint guards apply. Stop the normal service
before using this offline action.

## Recorded arithmetic

The existing verified projection snapshot reader supplies saved account values
and the latest retained version of every deal from one audited read. Each stable
deal identity contributes once. Corrected reported amounts replace their older
contribution; the report retains the revision count. Native BUY/SELL fill kinds
and cash kinds are grouped separately with deal counts and all four signed
component totals. Positive rebates remain positive; negative costs remain
negative. Allocations route each reported component to one declared bucket.
Each row verifies that balance plus credit movement equals the four reported
component totals; the overall summary verifies the sum of its rows.

Expected balance/credit equal opening plus allocated recorded movement.
Differences are observed minus expected. No account currency conversion,
minor-unit rounding, current position profit/swap addition, or use of equity/
margin as cash takes place. Arithmetic uses a private 64-digit Decimal context;
source amounts outside the numeric envelope are withheld. Bounded eight-place
rollup fields cap at 100 quadrillion, enough for the 4,096 retained version
limit and four supported component fields without truncating valid sums.

CONSISTENT_WITH_DECLARATION requires both differences to be zero. DIVERGENT
retains independent balance and credit differences. WITHHELD carries no rows or
summary. Refusal covers wrong opening scope/start/currency, missing prior deals,
foreign/invalid source, missing history, a latest window ending before the full
retained coverage endpoint, currency conflicts, missing allocation rules,
unsupported numeric values, cancellation accounting or unavailable storage.

The result binds the declaration hash, latest receipt, scope, currency, history
interval and saved account observation time. Its wire model verifies row/summary
conservation and state agreement, fixes native/history/account freshness
attestation UNVERIFIED and preserves NON_ATOMIC_SEQUENTIAL_READ. Trading and
portfolio authority are fixed false. Exit 0 means agreement with the declaration;
exit 2 means divergent/withheld or a sanitized command refusal. The local output
contains account amounts and remains a private operator artifact.

## Native cancellation boundary

MetaQuotes documents separate profit, commission, swap and fee fields. For a
cancelled BUY/SELL deal, it describes changing the original kind, zeroing its
profit and posting a separate balance operation. See the primary
[deal properties](https://www.mql5.com/en/docs/constants/tradingconstants/dealproperties)
and [account properties](https://www.mql5.com/en/docs/constants/environment_state/accountinformation).

This makes a universal latest-value cash ledger unsafe to infer: it cannot
establish the original posted cash and its separate adjustment from latest
values alone. Therefore any latest cancelled trade withholds arithmetic with
CANCELLATION_ACCOUNTING_UNVERIFIED, even when a bucket rule is supplied. The wire
validator also refuses an arithmetic result claiming a nonzero cancellation
count. Retained original revisions are preserved for future independently proved
native adjustment linkage; this increment grants no such linkage. Position-lot
comparison keeps its existing separate cancellation behavior.

## Verification and remaining work

Tests use real normalized native doubles, audited SQLite projections and a real
offline command subprocess. They cover deposits, separate credit/profit and fee
allocation, rebates/swaps, corrected amounts counted once, declared opening
differences, cancelled/missing deals, absent allocation, exact opening binding,
duplicate rules/JSON, numeric limits, caller precision, closed storage, wire
conservation/authority, and unchanged heartbeat, controller revision and gateway
attempt count. No actual SDK, terminal, real credentials or broker is used.

Agreement is descriptive under declared opening/classification and unverified
native history. It proves no complete cash posting ledger, financing semantics,
corporate-action treatment, cancellation settlement, broker freshness, equity
reconciliation, portfolio accuracy or trading eligibility. The saved sequential
native account/history reads can be non-atomic and expired. Existing wire schemas,
journal formats, dependencies and paper domain contracts remain unchanged; two
schemas and the command are additive. Rollback retains journals and declaration/
report artifacts, and removes only this added read path. Native cash/correction
acceptance, portfolio integration, custody, capacity rollover, installed services,
independent review, browser acceptance and all P13 release gates remain pending.

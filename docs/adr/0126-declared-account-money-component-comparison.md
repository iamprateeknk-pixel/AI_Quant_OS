# ADR 0126 — Compare reported money under explicit component allocations

Status: implemented locally; independent/native acceptance pending.

## Decision

Use one verified retained projection snapshot to group latest reported profit,
commission, swap and fee values. Require original account/start/currency-bound
opening balance/credit and explicit bucket allocation for every observed kind.
Keep signs, latest-version identity, bounded exact arithmetic and all denied
authority. Report agreement only with the declaration, never native completeness.

Withhold latest cancelled trade money arithmetic. MetaQuotes documents zeroed
original profit and a separate balance adjustment; latest values alone do not
establish original posted cash. Preserve versions for future proved linkage.
Missing prior deals are not erased or treated as cancellations.

## Consequences

The offline command advances account-money reconciliation without mutating
portfolios or original scheduling/audit history. Opening and allocation remain
unverified, and current contract units, financing/equity, cancellation posting
and native completeness remain separate work. Existing schemas, journal formats
and domain contracts remain compatible; rollback keeps all retained evidence.

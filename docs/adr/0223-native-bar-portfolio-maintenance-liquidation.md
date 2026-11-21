# ADR 0223: Native bar portfolio maintenance liquidation

**Status:** Accepted for deterministic offline PAPER diagnostics

## Context

The synchronized native bar portfolio admitted a basket when gross target
notional fit the starting USD balance, but it did not monitor equity as prices
moved or force-close positions. The pinned `CurrencyPair` instruments already
declare 1x initial and maintenance margin. The Nautilus backtest integration
does not automatically liquidate a breached basket.

## Decision

At every synchronized bar open, value each open position at the adverse
executable quote: bid for longs and ask for shorts. Compute quote-currency
equity from the initial USD balance, retained fill cash flows, marked inventory,
and USD commissions. Convert JPY balances to USD with the contemporaneous
USDJPY quote using the conservative side. Compute required maintenance margin
from current ask notional at the instrument's declared maintenance ratio.

When equity falls below aggregate maintenance margin, close every open leg with
native market orders at the current quote, retain the trigger time, pre-close
equity, required margin, and affected instruments, and stop the strategy cohort
for the remainder of the replay. The versioned report is schema 1.1; schema 1.0
reports remain readable and verifiable.

## Consequences

The behavior is deterministic and replayable for the observed bar-open quotes.
It cannot detect an intrabar breach between bars, model broker-specific margin
tiers, or establish live liquidation behavior. The result remains a PAPER
diagnostic with no promotion authority. Asynchronous/tick portfolio liquidation
and broker-calibrated acceptance remain open.

## Rollback

Revert the implementation and report-schema change together. Keep already
written schema 1.1 diagnostics immutable; if a reverted reader cannot parse
them, retain them for a schema 1.1-capable reader rather than rewriting or
downgrading their evidence.

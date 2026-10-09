# Native recorded-tick source evidence

## Delivered in increment 0130

The recorded-tick runner snapshots its canonical source before native engine construction and verifies the returned result before exposing it to callers or the existing CLI. Shared source validation rejects duplicates, mixed instruments/sources, ambiguous timestamps, unsupported quote precision, insufficient/nonintegral liquidity, synthetic slippage and unrepresentable fixed fees. The pinned native runtime and original result schema remain unchanged.

The separate evidence module has no Nautilus SDK, Pydantic or strategy execution dependency. It checks the original dataset fingerprint, instrument and quote currency, declared strategy reference, costs and fixed replay/P&L models. Native order count must equal the number of retained fills.

Each fill must occur at a unique, strictly increasing source event after the first quote. Buy fills equal that event's ask; sells equal its bid. Quantities must move between the declared flat/long/short targets with full fills, and each fee must equal the original fixed quote-currency fee. Duplicate events, partial quantities, altered prices/fees and position overflow are refused. USD fees retain cent precision; JPY fees retain whole-unit precision.

Independent cash reconstruction sums signed execution cash flows and fees, then adds the final position marked at the original last bid/ask midpoint. The result's final position and net P&L must match exactly under a private decimal context with inexact arithmetic rejected. No rounding is used to make damaged evidence agree. A malformed native callback observation raises rather than producing an export. Capturing the source prevents normal callback replacement of the caller's dataset from changing the retained replay binding.

## Evidence limits

This verifier checks retained fills against their declared source/execution model. It does not rerun a strategy to prove that the strategy requested those targets, prove that an omitted trade should have occurred, attest native process/source custody, or independently reconstruct the engine's separate realized-P&L field. That field is checked for finite representation and remains a native observation requiring replay evidence. No supplied candidate source is executed by this verifier.

The recorded-tick model remains single-instrument, complete target fills, one decision per source event, fixed fees in the quote currency, and zero synthetic slippage. Multi-instrument asynchronous tick portfolios, margin/liquidation, source attestation, isolated candidate execution, independent critical review and broader P03/P13 acceptance remain open. Trusted native regression fixtures and the existing CLI are exercised; live brokers, credentials, model providers and preserved uncertain candidate workers are untouched.

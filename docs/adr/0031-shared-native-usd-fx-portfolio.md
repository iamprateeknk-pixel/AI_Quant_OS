# ADR 0031: Replay synchronized FX strategies in one native USD account

Status: Accepted for local diagnostic replay.

Independent instrument engines do not prove a shared portfolio. Add a bounded trusted basket runner with one native engine, PAPER venue and USD margin account. Preserve leg source/genome bindings and actual fills. Stage all current opens before causal decisions, order equal-time decisions by instrument and retain exact quote cash flows plus explicitly converted final marks.

Nautilus 1.221.0's margin risk path bypasses free-balance checks. Enforce gross target notional admission using current quotes and rounded native USD balance, upward Decimal calculations, a single USD order fee and strictly reducing exposure allowed. Failure cannot produce a successful partial report.

Keep final quote marks separate from observed native rounded realized balance. A source-bound verifier checks fill times/prices, targets/positions, quote PnL, USD conversion and fees; fresh engine replay verifies the native observation. Preserve legacy native models and sealed sample sources.

Consequences: four pairs share a native account and explicit simulation collateral policy. Asynchronous/tick portfolios, liquidation, partial liquidity and production risk/live authority remain unfinished. The command retains immutable bounded exports and protects input/registry files. Rollback preserves source objects/results; promotion remains false.

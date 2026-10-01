# ADR 0029: Add an opt-in native long/short bar replay model

Status: Accepted for local diagnostic replay.

P03 needs actual short and reversal accounting. Preserve the original long/flat runner and sealed sample sources. Add a separate registered directional sample and explicit `BAR_OPEN_CLOSE_LONG_SHORT_V1` result model using the same causal open/close quotes and native execution engine.

Bound directional quantity to half the synthetic quote capacity. A full reversal submits one native order of twice the target quantity with one fixed fee. Reconcile actual fills and final close inventory in a shared verifier, and bind counterfactual and shadow reports to the parent's replay model.

Expose the new sample in the backtest and research commands. Research 1.0 can retain measured walk-forward/holdout/cost evidence while preserving all five pending gates. Reject 1.1/1.2 before reservation until reversal-fee resampling and directional adversarial policies are reviewed. Additive schemas accept the model and actual serialized Decimal exponent notation without changing historical artifact bytes.

Consequences: single-instrument diagnostic short replay becomes reproducible with retained source provenance. This does not implement integrated margin/currency simulation, market depth, latency, partial liquidity or isolated untrusted code. Research/candidate/shadow outputs remain unable to authorize promotion or orders. Rollback keeps archives and source objects; older decoders cannot read the new model.

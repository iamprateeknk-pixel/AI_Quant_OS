# Complete native realized-position history

## Delivered in increment 0131

All single-instrument native bar, directional-bar and recorded-tick runners now retain each closed-position observation before the netting cache can reuse a position. The native realized total includes every closed cycle plus the reported realized amount of any current open position, including its booked entry commission. Closed cache entries are not counted again.

The runner checks that the closed-event inventory exactly matches closures/reversals in its fill ledger, that close times advance, and that observed instruments and currencies agree. It then requires starting native balance plus the complete native realized total to equal the observed final native account balance. Missing/duplicate/altered closed observations cannot produce a successful result. The position/cash/source checks already present continue to apply.

The initial trusted USD reversal fixture exposed the bug: the old cache-only total was -0.26 although all fills ended flat at -0.32. A partly open version lost a closed loss and reported -0.05 instead of -0.11. Repeated flat/reopen cycles now conserve the actual native account balance. Tests exercise actual pinned Nautilus events, both native callback fault refusal and the prior tick/source, bar, portfolio and research paths.

## Monetary semantics

`native_realized_pnl` is the complete **native currency-rounded** observation. The independently reconstructed `net_pnl` remains exact fill cash plus the final source mark. They are different measures when positions are open or native monetary precision rounds trade results. The account-balance agreement checks native observations against one another; it does not replace exact decimal source/cash validation or independently attest the engine's floating-point accounting algorithm.

The native pin, public schemas, execution replay models, source fingerprints, strategies and fill/cash accounting remain unchanged. The corrected realized observation can change report bytes/hashes. Existing reports with cache-only observations therefore must not be treated as identical to new replay evidence.

## Historical evidence and migration

Original evaluation artifacts remain byte-identical. Four additive 0131 baselines record actual corrected trusted replays; three envelope artifacts bind their original archive hashes. The corrected plain research report retains its existing canonical serialization. Current native and positive counterfactual regression fixtures use corrected evidence. An original archived counterfactual parent correctly fails baseline reproducibility under the corrected runner; no exception normalizes, overwrites or adopts it.

For operational migration, preserve the original archive, hashes, strategy/source/data/policy bindings and ownership evidence. Obtain a new replay through the applicable admitted execution path and retain it with a new output and experiment identity. Proposed candidate code still requires the isolated Linux worker; this fix grants no host execution authority. Never silently replace an existing research/candidate archive or reuse an uncertain worker. Native source/version review, complete strategy decision certification and all existing promotion/release gates still apply.

Pinned primary implementation references reviewed alongside the installed source:

- [Nautilus 1.221.0 position lifecycle](https://github.com/nautechsystems/nautilus_trader/blob/v1.221.0/nautilus_trader/model/position.pyx)
- [Nautilus 1.221.0 execution engine](https://github.com/nautechsystems/nautilus_trader/blob/v1.221.0/nautilus_trader/execution/engine.pyx)

## Remaining acceptance

Retained source attestation, independent reconstruction of native rounding, async tick portfolios, margin/liquidation, isolated candidate/full native validation, independent critical review and P03/P13/full-system acceptance remain pending. No actual broker SDK, terminal, credentials, model provider, promotion or live capital is exercised. Preserved uncertain candidate jobs remain untouched.

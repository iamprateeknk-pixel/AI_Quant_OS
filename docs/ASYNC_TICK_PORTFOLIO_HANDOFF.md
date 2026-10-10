# Asynchronous recorded-quote native portfolio (0132)

## Delivered scope

Replay two to four declared FX instruments with independent recorded-quote
timestamps in one pinned Nautilus 1.221.0 USD margin account. The command uses
registered trusted tick builtins, with source bindings checked before and after
execution. This is historical event replay; it is not a concurrent network feed.

```text
ai-quant-tick-portfolio-backtest plan.json --output report.json
```

The equivalent source command is `python -m ai_quant_os.tick_portfolio_cli`.
The trading extra is required. Relative CSV paths resolve against the plan folder.
The default registry is `.strategy-registry` beside the output; `--registry`
selects an existing or new trusted implementation registry.

Example plan (use recorded quote CSVs with the existing tick CSV columns):

```json
{
  "schema_version": "1.0",
  "initial_balance_usd": "100000",
  "fee_per_order_usd": "0.10",
  "max_quote_age_seconds": 3,
  "legs": [
    {"csv": "eurusd.csv", "genome": {"strategy_ref": "tick-momentum@1.0.0", "input_kind": "TICK", "parameters": []}, "quantity": 10},
    {"csv": "gbpusd.csv", "genome": {"strategy_ref": "tick-momentum@1.0.0", "input_kind": "TICK", "parameters": []}, "quantity": 10}
  ]
}
```

The tick momentum builtin is an interface demonstration. Its results are not a
validated trading strategy. Proposed candidate source is never imported by this
command. There are no credentials, broker calls, provider calls or order authority.

## Timing, price and collateral model

- At a shared source timestamp, cache every instrument's quote before any strategy
  decision. A second quote event at source time plus one nanosecond dispatches the
  decision using prior own-instrument history. Instrument ordering is canonical.
  This declared priority is not measured execution latency. Source/fill reports
  retain original microsecond timestamps and recorded prices.
- Full market targets are -quantity, zero or +quantity; a reversal is twice the
  quantity. Buys fill at the current recorded ask and sells at the recorded bid.
  Input liquidity must cover a full reversal. Synthetic slippage is zero.
- New exposure requires fresh, nonfuture quotes for every held/proposed exposure.
  Gross USD notional uses each ask, and JPY notional uses a fresh USDJPY bid.
  Admission requires proposed gross notional plus the current order fee to fit
  the observed native USD account balance, or to reduce total gross notional.
- A reduction in the active instrument's absolute target requires that instrument's
  fresh quote and can proceed with an unrelated stale quote. Native acceptance is
  still required. A reversal of equal size is treated as new exposure.
- Source age uses original timestamps, with an explicit 1..300 second bound
  (default three). Final valuation requires every final leg quote to be within
  the same bound of the portfolio's final source timestamp. Missing/future/stale
  quotes or denied/rejected orders withhold the entire report.

This is a conservative one-times gross notional admission model. It does not
implement maintenance margin, liquidation, financing, production collateral
allocation or exchange lifecycle rules.

## Money and export

Source cash and final midpoint inventory reconstruct each leg's gross quote-
currency P&L. Positive JPY P&L divides by the final USDJPY ask; negative P&L divides
by its bid. USD fixed fees are subtracted separately, once per native order.
Arithmetic uses a local Decimal context of precision 28. The native USD balance
is an observed engine field, not the midpoint equity or an independently
reconstructed multi-currency accounting certificate.

Export contains the report and sorted raw CSV SHA-256 lineage. The report embeds
original source quotes, fills and trusted strategy bindings. Existing output is
immutable: identical bytes are accepted; differing bytes are refused using the
shared durable temporary-file/hard-link writer. Output cannot replace plan/CSV
files or live inside registry storage. Registry storage cannot contain inputs.
Existing result schemas and synchronized bar portfolio behavior are preserved.

New additive schemas:

- `schemas/native-tick-portfolio-plan.schema.json`
- `schemas/native-tick-portfolio-report.schema.json` (the nested `report` member)

Limits: 64 KiB unique-key JSON plan, 4 MiB per CSV, 2..4 unique instruments,
10,000 source quotes per leg, complete-reversal quantity at most 500,000, and
four legs/80,000 native quote events at most. USDJPY is mandatory for JPY valuation.

`verify_tick_portfolio_report` independently checks retained source/fill cash,
positions, final quote ages, currency conversion and totals. It does not rerun
strategy targets, attest native balance, reconstruct the original collateral
decisions, prove source custody or certify the native runtime. Those require
replaying the trusted source and further independent acceptance.

## Acceptance still pending

Independent critical review, isolated portfolio acceptance, production data
custody and feed timing, maintenance-margin/liquidation behavior, realistic
financing and native accounting attestation remain open. All P00–P13 remain
incomplete; this increment grants no promotion or live trading authority.

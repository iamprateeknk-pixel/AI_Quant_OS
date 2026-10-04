# Native signal-tape adapter

The adapter replays numeric targets against a retained validation package in
NautilusTrader 1.221.0. It consumes JSON, applies the package's declared synthetic
spread, slippage and percentage commissions, and returns checked fill/cash-flow
evidence. No proposed candidate Python or candidate tests are imported or run.

Its status is `UNATTESTED_NATIVE_REPLAY`. The target values are operator supplied;
their equivalence to the retained A09 source is unproved. Execution, isolation and
promotion flags remain false. The package's proposed-source execution adapter
remains unavailable, and A10 remains planned. This is a native replay component
for the eventual isolated workflow, not a ValidationScorecard or completed
candidate validation.

## Operator command

```powershell
python -m ai_quant_os.signal_tape_cli `
  --archive data-local/local-app/paper.validation-packages.sqlite3 `
  --package-ref validation-package:<digest> `
  --signals your-signal-tape.json `
  --quantity 10000 `
  --initial-balance 100000 `
  --cost-scenario BASE `
  --output your-native-replay.json
```

The existing archive must contain the requested package. Signal input is bounded
to 1 MiB, output to 2 MiB. The command refuses an existing output and never creates
a missing archive. Results include numeric targets and native fills; they contain
no raw CSV, candidate source or proposed test text. This local command uses the
operator's filesystem access. No HTTP endpoint or agent tool is installed.

`SignalTape` has package, candidate and holdout reference/fingerprint fields and
one decision for every holdout open. Each decision contains a UTC `execute_at`,
the SHA-256 fingerprint of the immediately preceding zero-to-two completed bars,
and a strict integer target of -1, 0 or 1. The first two targets must be zero. The
holdout begins with fresh history; it is not warmed with training observations.
The complete wire contract is in `docs/schemas/signal-tape.schema.json`.

Binding checks enforce exact package/candidate/data association, complete row
coverage, closed-history fingerprints and execution times. These checks do not
prove that an operator chose targets without seeing future data. A future
admitted worker must have independently enforced prefix-only inputs and retain
its source/execution attestation before any stronger claim can be made.

## Native model

- The simulated venue is PAPER. Quote events represent each source bar's open
  followed by close one nanosecond later. Targets execute at the bound open.
- The per-side price impact is half the full declared spread plus slippage.
  Native bid prices round down and ask prices round up to the instrument tick.
- Native `MakerTakerFeeModel` charges the declared commission percentage on each
  market fill's notional, including complete reversals. Native Money applies
  quote-currency precision; a sufficiently small fee can round to zero. Costs
  that cannot be represented in the declared arithmetic are rejected instead of
  silently underflowing into a zero rate.
- A fixed synthetic bid/ask size of 1,000,000 base units bounds complete reversals
  to 500,000 units. This is not source volume, broker depth or measured liquidity.
- Each run has one instrument and a synthetic margin account in that instrument's
  quote currency. EURUSD/GBPUSD report USD; USDJPY/EURJPY report JPY. No USD
  conversion is guessed. Starting balance and quantity are explicit run inputs.
- Nautilus margin risk bypasses its balance check. The adapter explicitly admits
  new exposure/reversals only when full target notional at the current ask fits
  quote equity after the proposed fee and conservative bid/ask mark. Reducing
  exposure can proceed. This declared bound is not a broker margin or liquidation
  policy; ongoing maintenance/liquidation is unimplemented.
- Net P&L is exact fill cash flows and native fees plus the remaining position
  marked at the final source close. It does not assume a final liquidation.
  `native_realized_pnl` is a native cache value and is explicitly not independently
  checked. Financing, account conversion and market authenticity remain missing.

`DECLARED_STRESS` multiplies spread, slippage and commissions by the original
plan's stress multiplier. BASE and stress reuse the exact numeric target tape.
Neither establishes how candidate source would behave under those conditions.

## Evidence and remaining work

`verify_signal_replay` independently reconstructs required orders, open prices,
native fee amounts, target positions, collateral admission and cash flows from
the package and declared tape. It rejects changed prices/fees/costs/marks/targets
even if a forged report adjusts net P&L consistently. It is a mathematical
reconciliation, not an independent critical reviewer or source execution proof.

Actual native tests verify long-to-short reversal prices and fees, cost stress,
four-instrument quote currencies, collateral refusal, exact repeated results,
forgery rejection, warmup/history/time/coverage/type/authority denial, precision
underflow refusal, actual CLI execution and immutable output, byte limits and
missing-archive refusal. The synthetic target vectors deliberately exercise
adapter mechanics; no equivalence to A09's hypothesis-direction source is claimed.
The retained replay schema validates actual native output and rejects false
attestation. Full regression/preservation results are recorded in BUILD_STATUS.

Isolated compilation, proposed tests, prefix-only candidate workers and their
runtime/source attestation, actual candidate-generated targets, full statistical
validation and acceptance, measured costs, broker models, independent review,
promotion and production release gates remain open. SANDBOX naming does not
establish kernel confinement. Unattested replay artifacts must never be supplied
as source-verified research or promotion evidence.

# Shared native FX portfolio replay

`ai-quant-portfolio-backtest` runs two to four trusted bar strategies in one pinned NautilusTrader engine, one PAPER venue and one USD margin account. Each instrument retains its canonical source, genome/source binding, quantity, native fills and position. Instruments share collateral and actual per-order USD commissions.

## Run locally

```powershell
ai-quant-portfolio-backtest tests/fixtures/native_portfolio_plan.json --output data-local/native-portfolio.json
python -m pytest -q tests/test_native_portfolio.py
```

Plan version 1.0 has two to four CSV/genome/quantity/slippage entries, a starting USD balance and a per-order USD fee. Relative CSV paths resolve from the plan directory. Inputs are bounded to 4,096 canonical bars per instrument and synchronized UTC timestamps. Unique supported pairs are EURUSD, GBPUSD, USDJPY and EURJPY. A USDJPY strategy/source leg is required when JPY PnL needs conversion; missing prices are never inferred or fetched.

At each timestamp all current synthetic opening quotes enter the native cache before any strategy decides. Strategies receive only prior completed bars. Orders execute at the adverse current open quote; current closes then enter history. Equal-time decisions follow canonical instrument order, so changing input declaration order preserves results. Directional quantities are capped at 500,000 to fit complete 2Q reversals inside synthetic 1,000,000-unit quote capacity. Legacy long/flat targets remain unchanged.

## Shared collateral

The installed Nautilus 1.221.0 margin branch in `risk/engine.pyx` bypasses free-balance checks. A native engine alone therefore does not prove aggregate collateral enforcement. The wrapper checks each order before submission against the account's current rounded USD balance.

The bound sums every resulting position's absolute quantity times current ask notional. JPY notionals convert through current USDJPY bid; calculations round upward at 28-digit precision. New/equal-size exposure plus its USD fee must fit the shared balance. Strictly reducing gross exposure remains allowed if existing positions exceed the bound. There is no hedge offset, leverage beyond 1x or automatic forced liquidation. Denial fails the entire diagnostic instead of returning a successful partial basket.

This is offline simulation policy and does not issue platform Risk Kernel approvals or broker orders. Native margin/realized balances are engine observations, not production broker evidence.

## PnL and currency units

Commissions are USD for every pair. Each leg's gross quote PnL is exact fill cash flows plus final canonical close inventory. USD quotes require no conversion. JPY gains divide by final synthetic USDJPY ask and JPY losses by its bid, using the declared 28-digit Decimal context. Fees are deducted once in USD after conversion. Leg contributions reconcile to portfolio marked PnL.

`native_balance_usd` separately records the rounded native balance, including realized outcomes at execution-time conversion rates. It excludes the same final marked inventory used by the diagnostic, and differs from starting balance plus marked PnL. The retained arithmetic verifier checks source/fill/position/mark/fee totals; verifying this observed native balance requires fresh engine replay.

## Evidence and remaining work

[The baseline](evaluations/native-portfolio-baseline.json) retains all source snapshots/bindings and raw CSV fingerprints. Two fresh installed-command workspaces exported identical bytes and validated against [the schema](schemas/native-portfolio-export.schema.json). From 100,000 USD initial collateral it records 12 orders, 1.20 USD fees, -17.07794462011680554726912342 USD marked PnL and 99,986.11 USD observed native balance. These synthetic figures verify interfaces/accounting without a performance claim.

Tests verify one-engine execution, native USD fees/reversals, shared collateral refusal, current opening FX before cross-pair decisions, input-order reproducibility, source/cash tamper refusal, missing FX/asynchronous/duplicate-instrument refusal, strict quantities, caller precision isolation, command lineage/overwrite protection and actual retained engine replay. Existing single-instrument defaults and sealed manifests stay unchanged.

Remaining work includes asynchronous/tick portfolios, depth/latency, partial fills, margin-call/liquidation policy, isolated untrusted code, catalog integration, production shadow ingress, portfolio research selection and independent review. Promotion stays false. Rollback preserves source objects/exports; existing single-instrument reports keep their models. Raw fixture CSV bytes are preserved on checkout for stable fingerprints.

# ADR 0003 Currency ledger and valuation

Status: Accepted for paper portfolio reporting. The paper API currency gate is recorded in ADR 0004.

The portfolio engine uses explicit instrument base and quote currencies. Every fill adds signed base units and the opposite quote cash flow; fees reduce quote cash. Currency balances are combined across instruments before conversion to the reporting currency. Positive balances use conversion bid prices and liabilities use asks. Direct and inverse FX pairs are supported. Missing, stale, future-dated or untrusted conversion rates prevent aggregate valuation. The engine does not infer metadata from instrument names or select arbitrary conversion chains.

Pending orders contribute an interval in each currency. Unfilled buys and sells can execute independently, so they do not cancel in the interval. The remaining quantity is read with fills in one SQLite read transaction. The quote cash bound includes the intent's allowed slippage. Bounds are valued using current fresh prices; they do not guarantee future FX prices or model unestimated future fees. Gross worst case currency exposure sums the absolute currency bounds at conservative conversion asks and counts both currency legs. It is different from gross instrument notional.

The version 1.0 `/api/v1/portfolio/currencies` read contract exposes currency balances, pending intervals, valuation availability and a fingerprint of its inputs. Decimal values are strings. The paper API currently configures EURUSD metadata only; the engine tests cover multiple instruments and shared currencies. The existing EURUSD unit limit still governs trading. Before adding more paper instruments, the risk kernel and reservation transaction must enforce aggregate currency limits using the same valuation context.

This change adds no tables. Existing receipts supply quantities and slippage limits. Rollback restores the previous reporting endpoint and preserves the order and fill ledger. Production instrument metadata, conversion feeds, account funding, margin and live account equity remain later integrations.

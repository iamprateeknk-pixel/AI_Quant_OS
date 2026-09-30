from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from ai_quant_os.currency_portfolio import (
    CurrencyInstrument,
    CurrencyPendingOrder,
    FxRate,
    summarize_currency_portfolio,
)
from ai_quant_os.portfolio import PaperFill


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
INSTRUMENTS = (
    CurrencyInstrument("EURUSD", "EUR", "USD"),
    CurrencyInstrument("GBPUSD", "GBP", "USD"),
    CurrencyInstrument("EURJPY", "EUR", "JPY"),
)


def fill(key: str, instrument: str, side: str, quantity: str, price: str, fee="0"):
    return PaperFill(
        key, key, instrument, side, Decimal(quantity), Decimal(price), Decimal(fee), NOW
    )


def rate(base: str, quote: str, bid: str, ask: str, *, age=0, trusted=True):
    return FxRate(base, quote, Decimal(bid), Decimal(ask), NOW - timedelta(seconds=age), trusted)


def summarize(fills=(), pending=(), rates=()):
    return summarize_currency_portfolio(
        fills,
        pending=pending,
        instruments=INSTRUMENTS,
        rates=rates,
        reporting_currency="USD",
        now=NOW,
        max_rate_age_seconds=5,
    )


def test_multiple_instruments_aggregate_shared_currency_and_fees():
    result = summarize(
        (
            fill("eu", "EURUSD", "BUY", "100", "1.10", "0.10"),
            fill("gb", "GBPUSD", "SELL", "50", "1.30", "0.20"),
        ),
        rates=(rate("EUR", "USD", "1.15", "1.16"), rate("GBP", "USD", "1.24", "1.25")),
    )
    balances = {item.currency: item for item in result.currencies}
    assert balances["EUR"].filled_balance == Decimal("100")
    assert balances["GBP"].filled_balance == Decimal("-50")
    assert balances["USD"].filled_balance == Decimal("-45.30")
    assert result.marked_pnl_reporting == Decimal("7.20")
    assert result.valuation_available is True


def test_pending_orders_bound_each_currency_without_netting_unfilled_sides():
    result = summarize(
        pending=(
            CurrencyPendingOrder("buy", "EURUSD", "BUY", Decimal("100")),
            CurrencyPendingOrder("sell", "EURUSD", "SELL", Decimal("80")),
        ),
        rates=(rate("EUR", "USD", "1.10", "1.11"),),
    )
    balances = {item.currency: item for item in result.currencies}
    assert balances["EUR"].minimum_balance == Decimal("-80")
    assert balances["EUR"].maximum_balance == Decimal("100")
    assert balances["USD"].minimum_balance == Decimal("-111")
    assert balances["USD"].maximum_balance == Decimal("88")
    assert balances["EUR"].worst_case_exposure_reporting == Decimal("111")


@pytest.mark.parametrize(
    "rates,reason",
    [
        ((), "MISSING_FX_RATE"),
        ((rate("EUR", "USD", "1.1", "1.2", age=6),), "STALE_FX_RATE"),
        ((rate("EUR", "USD", "1.1", "1.2", age=-1),), "STALE_FX_RATE"),
        ((rate("EUR", "USD", "1.1", "1.2", trusted=False),), "UNTRUSTED_FX_RATE"),
    ],
)
def test_missing_stale_future_or_untrusted_rates_do_not_produce_totals(rates, reason):
    result = summarize((fill("eu", "EURUSD", "BUY", "100", "1.1"),), rates=rates)
    assert result.valuation_available is False
    assert result.marked_pnl_reporting is None
    assert result.gross_worst_case_exposure_reporting is None
    assert reason in result.issues


def test_inverse_conversion_handles_quote_currency_and_negative_liability():
    result = summarize(
        (fill("ej", "EURJPY", "BUY", "100", "160"),),
        rates=(rate("EUR", "USD", "1.1", "1.2"), rate("USD", "JPY", "150", "151")),
    )
    balances = {item.currency: item for item in result.currencies}
    assert balances["JPY"].marked_balance_reporting == Decimal("-16000") / Decimal("150")
    assert result.valuation_available


def test_duplicate_fills_and_unknown_instruments_are_rejected():
    same = fill("same", "EURUSD", "BUY", "100", "1.1")
    with pytest.raises(ValueError, match="Duplicate fill"):
        summarize((same, same))
    with pytest.raises(ValueError, match="Unknown instrument"):
        summarize((fill("unknown", "UNKNOWN", "BUY", "1", "1"),))


@given(
    buy=st.integers(min_value=1, max_value=100000),
    sell=st.integers(min_value=1, max_value=100000),
    buy_percent=st.integers(min_value=0, max_value=100),
    sell_percent=st.integers(min_value=0, max_value=100),
)
def test_every_partial_fill_combination_stays_inside_currency_bounds(
    buy, sell, buy_percent, sell_percent
):
    result = summarize(
        pending=(
            CurrencyPendingOrder("buy", "EURUSD", "BUY", Decimal(buy), Decimal("0.01")),
            CurrencyPendingOrder("sell", "EURUSD", "SELL", Decimal(sell), Decimal("0.01")),
        ),
        rates=(rate("EUR", "USD", "1.10", "1.11"),),
    )
    partial_buy = Decimal(buy) * Decimal(buy_percent) / 100
    partial_sell = Decimal(sell) * Decimal(sell_percent) / 100
    balances = {item.currency: item for item in result.currencies}
    actual_eur = partial_buy - partial_sell
    actual_usd = -partial_buy * Decimal("1.12") + partial_sell * Decimal("1.11")
    assert balances["EUR"].minimum_balance <= actual_eur <= balances["EUR"].maximum_balance
    assert balances["USD"].minimum_balance <= actual_usd <= balances["USD"].maximum_balance


def test_snapshot_identity_is_independent_of_input_order():
    fills = (fill("eu", "EURUSD", "BUY", "100", "1.1"), fill("gb", "GBPUSD", "SELL", "50", "1.3"))
    rates = (rate("EUR", "USD", "1.1", "1.2"), rate("GBP", "USD", "1.3", "1.4"))
    assert (
        summarize(fills, rates=rates).snapshot_id
        == summarize(fills[::-1], rates=rates[::-1]).snapshot_id
    )

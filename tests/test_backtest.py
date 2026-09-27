from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ai_quant_os.backtest import BacktestCosts, run_backtest
from ai_quant_os.market_data import Bar, BarDataset


class BuyAfterFirstBar:
    def target_position(self, history: tuple[Bar, ...]) -> int:
        return 1


def dataset() -> BarDataset:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return BarDataset(
        Bar(
            "EURUSD",
            start + timedelta(minutes=index),
            Decimal(price),
            Decimal(price) + 1,
            Decimal(price) - 1,
            Decimal(price),
            Decimal("100"),
        )
        for index, price in enumerate(("10", "11", "14"))
    )


def test_same_replay_produces_same_result() -> None:
    bars = dataset()
    costs = BacktestCosts(slippage_bps=Decimal("10"), fee_per_trade=Decimal("0.1"))
    first = run_backtest(bars, BuyAfterFirstBar(), costs)
    second = run_backtest(bars, BuyAfterFirstBar(), costs)
    assert first == second
    assert first.dataset_fingerprint == bars.fingerprint


def test_slippage_and_fee_reduce_profit() -> None:
    bars = dataset()
    free = run_backtest(bars, BuyAfterFirstBar(), BacktestCosts(Decimal(0), Decimal(0)))
    paid = run_backtest(bars, BuyAfterFirstBar(), BacktestCosts(Decimal("10"), Decimal("0.1")))
    assert free.net_pnl == Decimal("3")
    assert paid.net_pnl == Decimal("2.889")
    assert paid.net_pnl < free.net_pnl


def test_signal_executes_on_next_bar_not_same_bar() -> None:
    result = run_backtest(dataset(), BuyAfterFirstBar(), BacktestCosts(Decimal(0), Decimal(0)))
    assert result.fills[0].price == Decimal("11")


def test_empty_dataset_produces_no_trade() -> None:
    result = run_backtest(BarDataset([]), BuyAfterFirstBar(), BacktestCosts(Decimal(0), Decimal(0)))
    assert result.net_pnl == Decimal(0)
    assert result.fills == ()

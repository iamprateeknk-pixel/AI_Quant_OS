from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ai_quant_os.market_data import Bar, BarDataset


class Strategy(Protocol):
    def target_position(self, history: tuple[Bar, ...]) -> int: ...


@runtime_checkable
class StreamingStrategy(Protocol):
    """Opt-in bar strategy that maintains its own bounded state between callbacks."""

    def on_bar(self, bar: Bar) -> None: ...

    def target_position(self) -> int: ...


@dataclass(frozen=True)
class BacktestCosts:
    slippage_bps: Decimal
    fee_per_trade: Decimal

    def __post_init__(self) -> None:
        if (
            not self.slippage_bps.is_finite()
            or not self.fee_per_trade.is_finite()
            or self.slippage_bps < 0
            or self.fee_per_trade < 0
        ):
            raise ValueError("Backtest costs must be finite and nonnegative")


@dataclass(frozen=True)
class BacktestFill:
    observed_at: datetime
    side: str
    price: Decimal
    fee: Decimal


@dataclass(frozen=True)
class BacktestResult:
    dataset_fingerprint: str
    fills: tuple[BacktestFill, ...]
    net_pnl: Decimal
    final_position: int


def run_backtest(dataset: BarDataset, strategy: Strategy, costs: BacktestCosts) -> BacktestResult:
    history: list[Bar] = []
    fills: list[BacktestFill] = []
    cash = Decimal(0)
    position = 0
    instrument: str | None = None
    for bar in dataset.replay():
        if instrument is None:
            instrument = bar.instrument_id
        elif bar.instrument_id != instrument:
            raise ValueError("A backtest dataset must contain one instrument")
        if history:
            target = strategy.target_position(tuple(history))
            if target not in (0, 1):
                raise ValueError("This paper backtest supports flat or long targets")
            slip = costs.slippage_bps / Decimal(10_000)
            if target == 1 and position == 0:
                price = bar.open * (Decimal(1) + slip)
                cash -= price + costs.fee_per_trade
                position = 1
                fills.append(BacktestFill(bar.observed_at, "BUY", price, costs.fee_per_trade))
            elif target == 0 and position == 1:
                price = bar.open * (Decimal(1) - slip)
                cash += price - costs.fee_per_trade
                position = 0
                fills.append(BacktestFill(bar.observed_at, "SELL", price, costs.fee_per_trade))
        history.append(bar)
    mark = history[-1].close if history else Decimal(0)
    return BacktestResult(dataset.fingerprint, tuple(fills), cash + position * mark, position)

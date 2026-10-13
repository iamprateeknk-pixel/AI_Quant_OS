"""Typed result contracts for optional native backtest execution."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from ai_quant_os.backtest import BacktestCosts


@dataclass(frozen=True)
class NativeFill:
    observed_at: datetime
    side: Literal["BUY", "SELL"]
    price: Decimal
    quantity: Decimal
    fee: Decimal


@dataclass(frozen=True)
class NautilusResult:
    schema_version: Literal["1.0"]
    engine: Literal["NAUTILUS_BACKTEST"]
    engine_version: Literal["1.221.0"]
    replay_model: Literal[
        "BAR_OPEN_CLOSE_QUOTES_V1", "BAR_OPEN_CLOSE_LONG_SHORT_V1", "RECORDED_QUOTE_TICKS_V1"
    ]
    venue: Literal["PAPER"]
    instrument_id: str
    pnl_currency: str
    strategy_ref: str
    dataset_fingerprint: str
    costs: BacktestCosts
    fills: tuple[NativeFill, ...]
    native_order_count: int
    final_position: Decimal
    net_pnl: Decimal
    pnl_method: Literal[
        "EXACT_FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK",
        "EXACT_FILL_CASHFLOWS_PLUS_FINAL_QUOTE_MIDPOINT",
    ]
    native_realized_pnl: Decimal

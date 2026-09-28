from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from ai_quant_os.contracts import _require_aware


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    order_id: str
    instrument_id: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    price: Decimal
    fee: Decimal
    observed_at: datetime
    source: Literal["PAPER_MANUAL", "PAPER_BROKER"] = "PAPER_MANUAL"

    def __post_init__(self) -> None:
        _require_aware(self.observed_at)
        if not self.fill_id or not self.order_id or not self.instrument_id:
            raise ValueError("Fill identifiers are required")
        if self.side not in ("BUY", "SELL"):
            raise ValueError("Unsupported fill side")
        if self.source not in ("PAPER_MANUAL", "PAPER_BROKER"):
            raise ValueError("Unsupported fill source")
        if not all(value.is_finite() for value in (self.quantity, self.price, self.fee)):
            raise ValueError("Fill terms must be finite")
        if self.quantity <= 0 or self.price <= 0 or self.fee < 0:
            raise ValueError("Invalid fill terms")


@dataclass(frozen=True)
class PortfolioExposure:
    snapshot_id: str
    instrument_id: str
    filled_net_quantity: Decimal
    pending_buy_quantity: Decimal
    pending_sell_quantity: Decimal

    def __post_init__(self) -> None:
        if not self.snapshot_id or not self.instrument_id:
            raise ValueError("Portfolio exposure identifiers are required")
        values = (
            self.filled_net_quantity,
            self.pending_buy_quantity,
            self.pending_sell_quantity,
        )
        if not all(value.is_finite() for value in values):
            raise ValueError("Portfolio exposure must be finite")
        if self.pending_buy_quantity < 0 or self.pending_sell_quantity < 0:
            raise ValueError("Pending exposure cannot be negative")

    @classmethod
    def empty(cls, instrument_id: str = "EURUSD") -> "PortfolioExposure":
        return cls("paper:empty", instrument_id, Decimal(0), Decimal(0), Decimal(0))

    @property
    def worst_case_quantity(self) -> Decimal:
        return max(
            abs(self.filled_net_quantity + self.pending_buy_quantity),
            abs(self.filled_net_quantity - self.pending_sell_quantity),
        )

    def with_order(self, side: Literal["BUY", "SELL"], quantity: Decimal) -> Decimal:
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("Order quantity must be positive and finite")
        if side == "BUY":
            return max(
                abs(self.filled_net_quantity + self.pending_buy_quantity + quantity),
                abs(self.filled_net_quantity - self.pending_sell_quantity),
            )
        if side == "SELL":
            return max(
                abs(self.filled_net_quantity + self.pending_buy_quantity),
                abs(self.filled_net_quantity - self.pending_sell_quantity - quantity),
            )
        raise ValueError("Unsupported order side")


@dataclass(frozen=True)
class PositionSummary:
    instrument_id: str | None
    net_quantity: Decimal
    cash_flow_quote: Decimal
    gross_exposure_quote: Decimal | None
    net_exposure_quote: Decimal | None
    marked_pnl_quote: Decimal | None


def summarize_position(fills: tuple[PaperFill, ...], *, mark: Decimal | None) -> PositionSummary:
    if mark is not None and (not mark.is_finite() or mark <= 0):
        raise ValueError("Mark must be positive and finite")
    instruments = {fill.instrument_id for fill in fills}
    if len(instruments) > 1:
        raise ValueError("Portfolio summary requires one instrument and quote currency")
    net_quantity = sum(
        (fill.quantity if fill.side == "BUY" else -fill.quantity for fill in fills),
        Decimal(0),
    )
    cash_flow = sum(
        (
            -fill.quantity * fill.price - fill.fee
            if fill.side == "BUY"
            else fill.quantity * fill.price - fill.fee
            for fill in fills
        ),
        Decimal(0),
    )
    net_exposure = net_quantity * mark if mark is not None else None
    return PositionSummary(
        instrument_id=next(iter(instruments), None),
        net_quantity=net_quantity,
        cash_flow_quote=cash_flow,
        gross_exposure_quote=abs(net_exposure) if net_exposure is not None else None,
        net_exposure_quote=net_exposure,
        marked_pnl_quote=cash_flow + net_exposure if net_exposure is not None else None,
    )

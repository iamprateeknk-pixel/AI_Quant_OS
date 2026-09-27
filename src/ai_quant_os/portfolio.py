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

    def __post_init__(self) -> None:
        _require_aware(self.observed_at)
        if not self.fill_id or not self.order_id or not self.instrument_id:
            raise ValueError("Fill identifiers are required")
        if self.side not in ("BUY", "SELL"):
            raise ValueError("Unsupported fill side")
        if not all(value.is_finite() for value in (self.quantity, self.price, self.fee)):
            raise ValueError("Fill terms must be finite")
        if self.quantity <= 0 or self.price <= 0 or self.fee < 0:
            raise ValueError("Invalid fill terms")


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

"""Bounded numerical intake for the configured local paper market universe."""

from decimal import Decimal

from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS


def require_paper_prices(instrument_id: str, prices: tuple[Decimal, ...]) -> None:
    if instrument_id not in {row.instrument_id for row in PAPER_INSTRUMENTS}:
        raise ValueError("Unknown paper instrument")
    if not prices or any(
        not isinstance(value, Decimal)
        or not value.is_finite()
        or not Decimal("1e-12") <= value <= Decimal("1e12")
        or len(value.as_tuple().digits) > 64
        for value in prices
    ):
        raise ValueError("Paper price exceeds numerical intake budget")

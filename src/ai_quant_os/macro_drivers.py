"""Dated descriptive policy-rate changes; no currency forecast or current-policy claim."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, localcontext
from typing import Literal

from ai_quant_os.contracts import _require_aware


def rate_receipt_status(received_at: datetime | None, as_of: datetime) -> str:
    _require_aware(as_of)
    if received_at is None:
        return "MISSING"
    _require_aware(received_at)
    return (
        "FUTURE_RECEIPT"
        if received_at > as_of
        else "OLD_RECEIPT"
        if as_of - received_at >= timedelta(seconds=300)
        else "RECENT_RECEIPT"
    )


@dataclass(frozen=True)
class RatePoint:
    period: date
    value: Decimal

    def __post_init__(self) -> None:
        if (
            type(self.period) is not date
            or not isinstance(self.value, Decimal)
            or not self.value.is_finite()
            or not -100 <= self.value <= 100
            or len(self.value.as_tuple().digits) > 20
            or not -9 <= self.value.as_tuple().exponent <= 9
        ):
            raise ValueError("Invalid bounded rate point")


@dataclass(frozen=True)
class RateDriver:
    rate_id: Literal["DFR", "MLFR", "MRR_FR"]
    currency: Literal["EUR"]
    observations: tuple[RatePoint, ...]
    cutoff: date
    latest_period: date | None
    latest_percent: Decimal | None
    previous_period: date | None
    previous_percent: Decimal | None
    change_percentage_points: Decimal | None
    change_basis_points: Decimal | None
    future_periods: tuple[date, ...]
    cutoff_basis: Literal["UTC_DATE_ONLY"] = "UTC_DATE_ONLY"
    current_applicability: Literal["UNASSESSED"] = "UNASSESSED"


def describe_rate(rate_id: str, points: tuple[RatePoint, ...], *, cutoff: date) -> RateDriver:
    if (
        rate_id not in {"DFR", "MLFR", "MRR_FR"}
        or type(cutoff) is not date
        or not isinstance(points, tuple)
        or not 1 <= len(points) <= 2
        or any(not isinstance(row, RatePoint) for row in points)
    ):
        raise ValueError("Invalid bounded rate driver input")
    periods = tuple(row.period for row in points)
    if tuple(sorted(set(periods))) != periods:
        raise ValueError("Ambiguous rate observation date order")
    observed = tuple(row for row in points if row.period <= cutoff)
    latest = observed[-1] if observed else None
    previous = observed[-2] if len(observed) == 2 else None
    with localcontext(Context(prec=50)):
        change = latest.value - previous.value if previous else None
        return RateDriver(
            rate_id,
            "EUR",
            points,
            cutoff,
            latest.period if latest else None,
            latest.value if latest else None,
            previous.period if previous else None,
            previous.value if previous else None,
            change,
            change * 100 if change is not None else None,
            tuple(row.period for row in points if row.period > cutoff),
        )

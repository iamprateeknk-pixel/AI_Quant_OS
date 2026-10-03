"""Descriptive log-return measurements; no liquidity or trading authorization."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Context, Decimal, localcontext
from typing import Literal

from ai_quant_os.market_data import Bar


@dataclass(frozen=True)
class VolatilityMetrics:
    status: Literal["AVAILABLE", "INSUFFICIENT"]
    bar_count: int
    return_count: int
    window_start: datetime | None
    window_end: datetime | None
    cadence_regular: bool | None
    cadence_seconds: Decimal | None
    mean_return_bps: Decimal | None
    standard_deviation_bps: Decimal | None
    latest_return_bps: Decimal | None
    abnormality_status: Literal[
        "AVAILABLE",
        "INSUFFICIENT_BASELINE",
        "ZERO_VARIANCE_MATCH",
        "DEGENERATE_BASELINE",
        "IRREGULAR_CADENCE",
    ]
    abnormality_score: Decimal | None


def _deviation(values: tuple[Decimal, ...]) -> tuple[Decimal, Decimal]:
    mean = sum(values) / len(values)
    return mean, (sum((value - mean) ** 2 for value in values) / len(values)).sqrt()


def volatility_features(bars: tuple[Bar, ...]) -> VolatilityMetrics:
    if (
        not isinstance(bars, tuple)
        or len(bars) > 64
        or any(not isinstance(bar, Bar) for bar in bars)
        or len({bar.instrument_id for bar in bars}) > 1
        or any(left.observed_at >= right.observed_at for left, right in zip(bars, bars[1:]))
        or any(
            not Decimal("1e-12") <= price <= Decimal("1e12") or len(price.as_tuple().digits) > 64
            for bar in bars
            for price in (bar.open, bar.high, bar.low, bar.close)
        )
    ):
        raise ValueError("Invalid bounded volatility history")
    if len(bars) < 3:
        return VolatilityMetrics(
            "INSUFFICIENT",
            len(bars),
            max(0, len(bars) - 1),
            bars[0].observed_at if bars else None,
            bars[-1].observed_at if bars else None,
            None,
            None,
            None,
            None,
            None,
            "INSUFFICIENT_BASELINE",
            None,
        )
    gaps = tuple(right.observed_at - left.observed_at for left, right in zip(bars, bars[1:]))
    regular = len(set(gaps)) == 1
    with localcontext(Context(prec=50)):
        returns = tuple(
            (right.close / left.close).ln() * 10000 for left, right in zip(bars, bars[1:])
        )
        mean, deviation = _deviation(returns)
        score = None
        status = "INSUFFICIENT_BASELINE"
        if not regular:
            status = "IRREGULAR_CADENCE"
        elif len(returns) >= 3:
            baseline_mean, baseline_deviation = _deviation(returns[:-1])
            distance = abs(returns[-1] - baseline_mean)
            if baseline_deviation == 0:
                status = "ZERO_VARIANCE_MATCH" if distance == 0 else "DEGENERATE_BASELINE"
                score = Decimal(0) if distance == 0 else None
            else:
                standardized = distance / baseline_deviation
                score = standardized / (1 + standardized)
                status = "AVAILABLE"
        return VolatilityMetrics(
            "AVAILABLE",
            len(bars),
            len(returns),
            bars[0].observed_at,
            bars[-1].observed_at,
            regular,
            Decimal(str(gaps[0].total_seconds())) if regular else None,
            mean,
            deviation,
            returns[-1],
            status,
            score,
        )

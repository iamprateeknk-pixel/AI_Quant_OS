"""Deterministic bar-window features and explicitly uncalibrated regime scores."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, localcontext

from ai_quant_os.market_data import Bar, BarDataset

WINDOWS = (("SHORT", 8, 3), ("MEDIUM", 24, 24), ("LONG", 64, 64))


@dataclass(frozen=True)
class HorizonFeatures:
    horizon: str
    status: str
    bar_count: int
    observed_span_seconds: Decimal | None
    start: datetime | None
    end: datetime | None
    return_bps: Decimal | None
    volatility_bps: Decimal | None
    efficiency: Decimal | None


def regime_features(bars: tuple[Bar, ...]) -> tuple[HorizonFeatures, ...]:
    bars = BarDataset(bars).bars
    if len({bar.instrument_id for bar in bars}) > 1:
        raise ValueError("Regime windows require one instrument")
    rows = []
    with localcontext() as context:
        context.prec = 28
        for name, count, minimum in WINDOWS:
            window = bars[-count:]
            if len(window) < minimum:
                rows.append(
                    HorizonFeatures(
                        name, "INSUFFICIENT", len(window), None, None, None, None, None, None
                    )
                )
                continue
            returns = tuple(
                (right.close / left.close - 1) * 10000 for left, right in zip(window, window[1:])
            )
            mean = sum(returns) / len(returns)
            volatility = (sum((x - mean) ** 2 for x in returns) / len(returns)).sqrt()
            path = sum(abs(right.close - left.close) for left, right in zip(window, window[1:]))
            efficiency = abs(window[-1].close - window[0].close) / path if path else Decimal(0)
            rows.append(
                HorizonFeatures(
                    name,
                    "AVAILABLE",
                    len(window),
                    Decimal(str((window[-1].observed_at - window[0].observed_at).total_seconds())),
                    window[0].observed_at,
                    window[-1].observed_at,
                    (window[-1].close / window[0].close - 1) * 10000,
                    volatility,
                    efficiency,
                )
            )
    return tuple(rows)


def regime_scores(features: HorizonFeatures) -> tuple[str, dict[str, Decimal]]:
    """Normalized feature strengths, not probabilities of empirical correctness."""
    labels = ("TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN")
    if features.status != "AVAILABLE":
        return "UNKNOWN", {name: Decimal(int(name == "UNKNOWN")) for name in labels}
    if any(
        value is None or not value.is_finite()
        for value in (features.return_bps, features.volatility_bps, features.efficiency)
    ):
        raise ValueError("Invalid regime feature values")
    with localcontext() as context:
        context.prec = 28
        change = abs(features.return_bps) / 20
        strengths = (
            change * features.efficiency,
            1 / (1 + change),
            features.volatility_bps / 25,
            Decimal(0),
        )
        total = sum(strengths)
        scores = {
            label: (value / total).quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN)
            for label, value in zip(labels, strengths)
        }
        # Put Decimal rounding remainder in the largest weight, never a negative UNKNOWN weight.
        winner = max(labels, key=scores.__getitem__)
        scores[winner] += 1 - sum(scores.values())
        return winner, scores

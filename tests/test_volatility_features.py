from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ai_quant_os.market_data import Bar

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def calculate(prices, times=None):
    from ai_quant_os.volatility_features import volatility_features

    bars = tuple(
        Bar(
            "EURUSD",
            NOW + timedelta(seconds=(times or range(len(prices)))[index]),
            price,
            price,
            price,
            price,
            Decimal(1),
        )
        for index, price in enumerate(map(Decimal, prices))
    )
    return volatility_features(bars)


def test_known_alternating_returns_have_half_abnormality_score():
    result = calculate(["1", "2", "1", "2"])
    assert result.status == "AVAILABLE" and result.return_count == 3
    assert abs(result.abnormality_score - Decimal("0.5")) < Decimal("1e-20")
    assert Decimal("6535") < result.standard_deviation_bps < Decimal("6536")
    assert Decimal("2310") < result.mean_return_bps < Decimal("2311")
    assert result.cadence_regular is True and result.cadence_seconds == 1


def test_zero_variance_change_does_not_invent_a_probability():
    flat = calculate(["1"] * 4)
    assert flat.standard_deviation_bps == 0 and flat.abnormality_score == 0
    assert flat.abnormality_status == "ZERO_VARIANCE_MATCH"
    jump = calculate(["1", "1", "1", "2"])
    assert jump.abnormality_status == "DEGENERATE_BASELINE"
    assert jump.abnormality_score is None


def test_irregular_cadence_withholds_latest_return_comparison():
    result = calculate(["1", "2", "1", "2"], [0, 1, 2, 4])
    assert result.cadence_regular is False and result.cadence_seconds is None
    assert result.abnormality_status == "IRREGULAR_CADENCE"
    assert result.abnormality_score is None


@pytest.mark.parametrize("prices", [[], ["1"], ["1", "2"]])
def test_missing_or_short_history_has_no_volatility(prices):
    result = calculate(prices)
    assert result.status == "INSUFFICIENT"
    assert result.standard_deviation_bps is None and result.abnormality_score is None


def test_three_bars_measure_volatility_but_not_abnormality_baseline():
    result = calculate(["1", "2", "1"])
    assert result.standard_deviation_bps > 0
    assert result.abnormality_status == "INSUFFICIENT_BASELINE"
    assert result.abnormality_score is None


def test_duplicate_timestamps_are_rejected_instead_of_deduplicating():
    calculate(["1"] * 3)
    with pytest.raises(ValueError):
        calculate(["1"] * 3, [0, 0, 1])

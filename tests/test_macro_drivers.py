from datetime import date
from decimal import Decimal, localcontext
import pytest


@pytest.mark.parametrize(
    "values,change", [("-0.5,0.25", "75"), ("2.5,2.25", "-25"), ("2.5,2.5", "0")]
)
def test_dated_rate_change_preserves_percent_levels_and_reports_basis_points(values, change):
    from ai_quant_os.macro_drivers import RatePoint, describe_rate

    first, last = map(Decimal, values.split(","))
    points = (RatePoint(date(2026, 6, 17), first), RatePoint(date(2026, 9, 16), last))
    with localcontext() as context:
        context.prec = 3
        result = describe_rate("DFR", points, cutoff=date(2026, 10, 3))
    assert result.latest_percent == last and result.change_basis_points == Decimal(change)
    assert result.change_percentage_points == last - first
    assert result.latest_period == date(2026, 9, 16)
    assert result.current_applicability == "UNASSESSED"


def test_future_dated_changes_are_retained_separately_without_using_their_value():
    from ai_quant_os.macro_drivers import RatePoint, describe_rate

    result = describe_rate(
        "DFR",
        (RatePoint(date(2026, 9, 16), Decimal("2.5")), RatePoint(date(2026, 10, 7), Decimal("3"))),
        cutoff=date(2026, 10, 3),
    )
    assert result.latest_percent == Decimal("2.5") and result.change_basis_points is None
    assert result.future_periods == (date(2026, 10, 7),)


def test_only_future_facts_cannot_supply_a_latest_observed_value():
    from ai_quant_os.macro_drivers import RatePoint, describe_rate

    result = describe_rate(
        "DFR", (RatePoint(date(2026, 10, 7), Decimal("3")),), cutoff=date(2026, 10, 3)
    )
    assert result.latest_percent is None and result.latest_period is None


@pytest.mark.parametrize("fault", ["duplicate", "order", "too_many", "numeric", "unknown_rate"])
def test_ambiguous_or_unbounded_rate_driver_inputs_are_refused(fault):
    from ai_quant_os.macro_drivers import RatePoint, describe_rate

    points = (
        RatePoint(date(2026, 6, 17), Decimal("2.25")),
        RatePoint(date(2026, 9, 16), Decimal("2.5")),
    )
    if fault == "duplicate":
        points = (points[0], points[0])
    elif fault == "order":
        points = tuple(reversed(points))
    elif fault == "too_many":
        points = points + (RatePoint(date(2026, 10, 7), Decimal(3)),)
    with pytest.raises(ValueError):
        if fault == "numeric":
            RatePoint(date(2026, 10, 3), Decimal("0e-1000000"))
        else:
            describe_rate(
                "UNKNOWN" if fault == "unknown_rate" else "DFR", points, cutoff=date(2026, 10, 3)
            )

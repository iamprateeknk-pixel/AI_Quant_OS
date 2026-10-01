from decimal import Decimal, Inexact, localcontext

import pytest


def series(values, position=Decimal(0)):
    from ai_quant_os.monte_carlo import ClosedTradeSeries

    return ClosedTradeSeries(tuple(Decimal(value) for value in values), position)


def test_trade_cycles_keep_native_fees_partial_exits_and_short_cashflows():
    from ai_quant_os.monte_carlo import CashFill, closed_trade_series

    fills = tuple(
        CashFill(side, Decimal(qty), Decimal(price), Decimal(fee))
        for side, qty, price, fee in (
            ("BUY", "2", "10", ".1"),
            ("SELL", "1", "11", ".1"),
            ("SELL", "1", "12", ".1"),
            ("SELL", "1", "12", ".1"),
            ("BUY", "1", "10", ".1"),
        )
    )
    result = closed_trade_series(fills)
    assert result.net_pnls == (Decimal("2.7"), Decimal("1.8"))
    assert result.open_position == 0


def test_reversals_require_explicit_fee_attribution():
    from ai_quant_os.monte_carlo import CashFill, closed_trade_series

    with pytest.raises(ValueError, match="reversal"):
        closed_trade_series(
            (
                CashFill("BUY", Decimal(1), Decimal(10), Decimal(0)),
                CashFill("SELL", Decimal(2), Decimal(12), Decimal(".1")),
            )
        )


def test_positive_and_negative_completed_trade_samples_pass_or_fail_declared_screen():
    from ai_quant_os.monte_carlo import MonteCarloSpec, run_monte_carlo

    spec = MonteCarloSpec(iterations=100, block_size=4, drawdown_limit=Decimal(10))
    positive = run_monte_carlo(series(["1"] * 20), spec)
    assert positive.state == "PASS" and positive.lower_tail_pnl == 20
    assert positive.drawdown_p95 == 0 and positive.positive_fraction == 1
    negative = run_monte_carlo(series(["-1"] * 20), spec)
    assert negative.state == "FAIL" and negative.drawdown_p95 == 20
    assert negative.positive_fraction == 0


def test_too_few_or_unclosed_trades_cannot_pass():
    from ai_quant_os.monte_carlo import MonteCarloSpec, run_monte_carlo

    assert run_monte_carlo(series(["1"] * 5), MonteCarloSpec()).state == "PENDING"
    result = run_monte_carlo(series(["1"] * 20, Decimal(1)), MonteCarloSpec())
    assert result.state == "PENDING" and result.reason == "UNCLOSED_POSITION"
    assert result.simulation_sha256 is None


def test_seeded_sampling_is_reproducible_and_independent_of_decimal_context():
    from ai_quant_os.monte_carlo import MonteCarloSpec, run_monte_carlo

    sample = series(["3", "-1", "-2", "2", "5", "-4"] * 7)
    first = run_monte_carlo(sample, MonteCarloSpec(seed=1, block_size=3, iterations=100))
    assert first == run_monte_carlo(sample, first.spec)
    other = run_monte_carlo(sample, MonteCarloSpec(seed=2, block_size=3, iterations=100))
    assert first.simulation_sha256 != other.simulation_sha256
    with localcontext() as context:
        context.prec = 4
        context.traps[Inexact] = True
        assert run_monte_carlo(sample, first.spec) == first


@pytest.mark.parametrize(
    "kwargs",
    [
        {"seed": True},
        {"iterations": 1},
        {"block_size": 0},
        {"min_closed_trades": 2},
        {"drawdown_limit": Decimal("NaN")},
    ],
)
def test_invalid_simulation_policy_is_rejected(kwargs):
    from ai_quant_os.monte_carlo import MonteCarloSpec

    with pytest.raises(ValueError):
        MonteCarloSpec(**kwargs)

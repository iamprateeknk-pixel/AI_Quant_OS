"""Tests for the Validation Fortress (validation_fortress.py).

All series are synthetic and fully seeded, so every verdict here is
deterministic. The price-driven tests share one helper: a simple
momentum rule (position = sign of the trailing lookback return, decided
at bar t, return earned t -> t+1) with a small proportional cost charged
in the period where the position changes.
"""

import math
import random
from dataclasses import replace

import pytest

from ai_quant_os.validation_fortress import (
    FortressConfig,
    StrategySeries,
    run_fortress,
)

LOOKBACKS = (5, 10, 20, 40)
BASE_LOOKBACK = 10
COST_RATE = 0.0005


def make_prices(bars, *, drift, volatility, seed):
    """Geometric price path: constant log drift plus seeded Gaussian noise."""
    rng = random.Random(seed)
    log_price = 0.0
    prices = [100.0]
    for _ in range(bars - 1):
        log_price += drift + rng.gauss(0.0, volatility)
        prices.append(100.0 * math.exp(log_price))
    return tuple(prices)


def momentum_series(prices, lookback, cost_rate=COST_RATE):
    """Run the momentum rule; return (net, gross, costs, trade_count).

    ``trade_count`` counts position units changed (entering from flat is
    1, a full long<->short flip is 2). Costs are ``cost_rate`` per unit
    changed, allocated to the period where the position changes.
    """
    gross = []
    costs = []
    trade_count = 0
    previous_position = 0
    for index in range(len(prices) - 1):
        if index >= lookback:
            trailing = prices[index] / prices[index - lookback] - 1.0
            position = 1 if trailing > 0 else (-1 if trailing < 0 else 0)
        else:
            position = 0
        change = abs(position - previous_position)
        trade_count += change
        gross.append(position * (prices[index + 1] / prices[index] - 1.0))
        costs.append(cost_rate * change)
        previous_position = position
    net = tuple(g - c for g, c in zip(gross, costs, strict=True))
    return net, tuple(gross), tuple(costs), trade_count


def series_from_prices(prices, *, n_trials=None):
    """Build a StrategySeries from prices, with lookback variants."""
    variants = {}
    base = None
    for lookback in LOOKBACKS:
        net, gross, costs, trades = momentum_series(prices, lookback)
        variants[f"lb{lookback}"] = net
        if lookback == BASE_LOOKBACK:
            base = (net, gross, costs, trades)
    assert base is not None
    net, gross, costs, trades = base
    return StrategySeries(
        net_returns=net,
        gross_returns=gross,
        period_costs=costs,
        trade_count=trades,
        periods_per_year=252.0,
        variant_net_returns=variants,
        n_trials=len(LOOKBACKS) if n_trials is None else n_trials,
    )


def trending_series():
    """Strongly drifting market: drift 0.0035/bar against 0.008 noise."""
    return series_from_prices(make_prices(620, drift=0.0035, volatility=0.008, seed=42))


def constant_series(periods=300, *, gross_value=0.0012, cost_value=0.0002):
    """Flat synthetic series with an exact gross - costs == net identity."""
    gross = tuple(gross_value for _ in range(periods))
    costs = tuple(cost_value for _ in range(periods))
    net = tuple(g - c for g, c in zip(gross, costs, strict=True))
    return StrategySeries(
        net_returns=net,
        gross_returns=gross,
        period_costs=costs,
        trade_count=150,
        periods_per_year=252.0,
        variant_net_returns={"base": net},
        n_trials=3,
    )


# --- headline behaviour -------------------------------------------------------


def test_trending_series_can_pass():
    # The synthetic trend is strong but the momentum rule only trades 45
    # units on it, so the trade floor is lowered; every other gate runs
    # at its default threshold.
    verdict = run_fortress(trending_series(), FortressConfig(min_trades=25))
    assert verdict.passed is True
    assert all(gate.passed for gate in verdict.gates)
    assert verdict.metrics["total_net_return"] > 0
    assert verdict.metrics["pbo"] <= 0.5
    assert verdict.metrics["deflated_sharpe"] >= 0.95


def test_pure_noise_fails():
    prices = make_prices(620, drift=0.0, volatility=0.01, seed=123)
    verdict = run_fortress(series_from_prices(prices))
    assert verdict.passed is False
    assert verdict.gate("EXPECTANCY").passed is False


def test_overfit_curve_fit_fails():
    periods = 400
    hero = tuple([0.012] * 160 + [-0.010] * 240)
    rng = random.Random(99)
    variants = {"hero": hero}
    for index in range(4):
        variants[f"noise{index}"] = tuple(rng.gauss(0.0, 0.004) for _ in range(periods))
    costs = tuple(0.0001 for _ in range(periods))
    gross = tuple(value + 0.0001 for value in hero)
    series = StrategySeries(
        net_returns=hero,
        gross_returns=gross,
        period_costs=costs,
        trade_count=200,
        periods_per_year=252.0,
        variant_net_returns=variants,
        n_trials=25,
    )
    verdict = run_fortress(series)
    assert verdict.passed is False
    stability_gates = ("WALK_FORWARD", "HOLDOUT", "CSCV_PBO")
    assert any(verdict.gate(name).passed is False for name in stability_gates)
    assert verdict.gate("WALK_FORWARD").passed is False
    assert verdict.gate("HOLDOUT").passed is False


# --- fail-closed behaviour ----------------------------------------------------


def test_insufficient_trades_fail_closed():
    series = replace(trending_series(), trade_count=2)
    verdict = run_fortress(series)  # must not raise
    assert verdict.passed is False
    assert verdict.gate("SAMPLE_SIZE").passed is False
    assert verdict.gate("DATA_INTEGRITY").passed is True


def test_empty_series_fails_closed():
    series = StrategySeries(
        net_returns=(),
        gross_returns=(),
        period_costs=(),
        trade_count=0,
        periods_per_year=252.0,
        variant_net_returns={},
        n_trials=1,
    )
    verdict = run_fortress(series)  # must not raise
    assert verdict.passed is False
    assert verdict.gate("DATA_INTEGRITY").passed is False
    for gate in verdict.gates[1:]:
        assert gate.passed is False
        assert gate.reason == "not evaluated: data integrity failed"


def test_nan_and_mismatched_lengths_fail_closed():
    base = constant_series()
    net_with_nan = list(base.net_returns)
    net_with_nan[5] = float("nan")
    nan_series = replace(base, net_returns=tuple(net_with_nan))
    short_variant = replace(base, variant_net_returns={"base": base.net_returns[:-1]})
    mismatched = replace(base, gross_returns=base.gross_returns[:-1])
    for series in (nan_series, short_variant, mismatched):
        verdict = run_fortress(series)  # must not raise
        assert verdict.passed is False
        assert verdict.gate("DATA_INTEGRITY").passed is False


def test_cost_accounting_mismatch_fails_integrity():
    base = constant_series()
    # Claim the gross return as net while still charging costs: the
    # identity net == gross - costs no longer holds.
    lying = replace(base, net_returns=base.gross_returns)
    verdict = run_fortress(lying)
    assert verdict.passed is False
    assert verdict.gate("DATA_INTEGRITY").passed is False
    assert "gross - costs" in verdict.gate("DATA_INTEGRITY").reason


def test_negative_costs_fail_integrity():
    base = constant_series()
    costs = list(base.period_costs)
    costs[0] = -0.5
    verdict = run_fortress(replace(base, period_costs=tuple(costs)))
    assert verdict.gate("DATA_INTEGRITY").passed is False


# --- individual gate behaviour -------------------------------------------------


def test_thin_edge_killed_by_cost_stress():
    # Net is a steady +0.0001/period, but gross is only 0.0011 against
    # 0.001 of costs: doubling costs turns the whole series negative.
    verdict = run_fortress(constant_series(gross_value=0.0011, cost_value=0.001))
    assert verdict.gate("EXPECTANCY").passed is True
    assert verdict.gate("COST_STRESS_2X").passed is False
    assert verdict.metrics["stressed_total_return"] < 0
    assert verdict.passed is False


def test_more_trials_lower_deflated_sharpe():
    series = trending_series()
    rng = random.Random(7)
    trial_sharpes = tuple(rng.gauss(0.0, 0.3) for _ in range(400))
    single = run_fortress(replace(series, n_trials=1))
    many = run_fortress(replace(series, n_trials=400, trial_sharpes=trial_sharpes))
    dsr_single = single.metrics["deflated_sharpe"]
    dsr_many = many.metrics["deflated_sharpe"]
    assert math.isfinite(dsr_single) and math.isfinite(dsr_many)
    assert dsr_many < dsr_single


def test_deflated_sharpe_needs_trial_dispersion():
    # One variant and no trial Sharpes: dispersion is not estimable.
    verdict = run_fortress(constant_series())
    gate = verdict.gate("DEFLATED_SHARPE")
    assert gate.passed is False
    assert gate.reason == "trial dispersion not estimable"
    assert math.isnan(verdict.metrics["deflated_sharpe"])


def test_cscv_needs_at_least_two_variants():
    verdict = run_fortress(constant_series())
    gate = verdict.gate("CSCV_PBO")
    assert gate.passed is False
    assert "not computable" in gate.reason
    assert math.isnan(verdict.metrics["pbo"])


# --- verdict surface ------------------------------------------------------------


def test_metrics_and_gate_accessors():
    verdict = run_fortress(trending_series(), FortressConfig(min_trades=25))
    expected_metrics = {
        "total_net_return",
        "total_gross_return",
        "total_costs",
        "mean_period_return",
        "annualized_sharpe",
        "max_drawdown",
        "holdout_total_return",
        "holdout_sharpe",
        "walk_forward_positive_fraction",
        "pbo",
        "deflated_sharpe",
        "stressed_total_return",
        "trade_count",
        "periods",
    }
    assert expected_metrics <= set(verdict.metrics)
    assert all(isinstance(value, float) for value in verdict.metrics.values())
    assert verdict.metrics["periods"] == 619.0
    assert verdict.gate("EXPECTANCY").gate == "EXPECTANCY"
    with pytest.raises(KeyError):
        verdict.gate("NO_SUCH_GATE")
    summary = verdict.summary()
    assert "\n" not in summary
    assert summary.startswith("PASS")


def test_verdict_is_deterministic():
    series = trending_series()
    first = run_fortress(series, FortressConfig(min_trades=25))
    second = run_fortress(series, FortressConfig(min_trades=25))
    assert first == second
    assert first.summary() == second.summary()

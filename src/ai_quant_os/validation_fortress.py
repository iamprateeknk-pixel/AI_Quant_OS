"""Validation Fortress: fail-closed statistical gates for strategy series.

A candidate strategy earns promotion only by passing every gate in this
module, in order:

1. ``DATA_INTEGRITY`` — the accounting identity ``net == gross - costs``
   holds period by period, all series share one non-zero length, every
   value is finite, costs are non-negative, and every parameter variant
   is aligned with the base series.
2. ``SAMPLE_SIZE`` — enough trades, periods and holdout periods.
3. ``EXPECTANCY`` — positive total net return, positive mean period
   return and positive annualised Sharpe.
4. ``WALK_FORWARD`` — the net series is split into sequential folds and
   a minimum fraction of folds must be profitable in total.
5. ``HOLDOUT`` — the final holdout segment must be profitable with a
   positive (per-period, non-annualised) Sharpe.
6. ``CSCV_PBO`` — Bailey / Lopez de Prado combinatorially symmetric
   cross-validation: the probability of backtest overfitting (PBO) must
   not exceed the configured maximum.
7. ``DEFLATED_SHARPE`` — Bailey & Lopez de Prado deflated Sharpe ratio:
   the observed Sharpe must survive a multiple-testing correction for
   the number of trials run in the experiment.
8. ``COST_STRESS_2X`` — the strategy must stay profitable when every
   period cost is doubled.

Fail-closed law: :func:`run_fortress` never raises on bad or degenerate
data and never passes a series with missing or insufficient data. Every
problem becomes a failed gate with a reason. If ``DATA_INTEGRITY``
fails, all remaining gates are reported as failed with the reason
``"not evaluated: data integrity failed"``.

Conventions used throughout:

- Fold and block splits distribute the remainder one extra period to
  the earliest folds/blocks, so every period is used exactly once.
- CSCV ranks are by total return with rank 1 = worst and rank M = best
  (M = number of variants); tied configs share their average rank. A
  combination counts as overfit when the in-sample best config's
  out-of-sample rank is at or below the median rank ``(M + 1) / 2``.
  Ties for in-sample best are broken by variant mapping order.
- Sharpe ratios use the sample standard deviation (ddof=1). A zero
  standard deviation yields Sharpe ``+inf`` when the mean is positive
  and ``0.0`` otherwise.
- Skewness and kurtosis in the deflated Sharpe are the adjusted
  Fisher-Pearson sample estimators (kurtosis is non-excess: the normal
  value is 3); below 4 periods the unadjusted moment ratios are used.
"""

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations
from statistics import NormalDist

#: Euler-Mascheroni constant, as used in the deflated Sharpe formula.
EULER_GAMMA = 0.5772156649

_GATE_ORDER = (
    "DATA_INTEGRITY",
    "SAMPLE_SIZE",
    "EXPECTANCY",
    "WALK_FORWARD",
    "HOLDOUT",
    "CSCV_PBO",
    "DEFLATED_SHARPE",
    "COST_STRESS_2X",
)

_METRIC_KEYS = (
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
)


@dataclass(frozen=True)
class FortressConfig:
    """Thresholds for every Validation Fortress gate."""

    min_trades: int = 100
    min_periods: int = 240
    holdout_fraction: float = 0.25
    min_holdout_periods: int = 50
    walk_forward_folds: int = 5
    walk_forward_min_positive_fraction: float = 0.6
    cscv_blocks: int = 8
    pbo_max: float = 0.5
    dsr_min: float = 0.95


@dataclass(frozen=True)
class StrategySeries:
    """One strategy's period returns plus its parameter neighbourhood.

    ``net_returns``, ``gross_returns`` and ``period_costs`` are aligned
    per-period series satisfying ``net[t] == gross[t] - costs[t]``.
    ``variant_net_returns`` maps variant names to their net period
    returns and must include the base configuration. ``n_trials`` is the
    total number of independent strategy trials run in the experiment
    (for the multiple-testing correction); ``trial_sharpes`` optionally
    carries the per-period Sharpe ratio of every trial, used to estimate
    trial dispersion for the deflated Sharpe gate.
    """

    net_returns: tuple[float, ...]
    gross_returns: tuple[float, ...]
    period_costs: tuple[float, ...]
    trade_count: int
    periods_per_year: float
    variant_net_returns: Mapping[str, tuple[float, ...]]
    n_trials: int
    trial_sharpes: tuple[float, ...] = ()


@dataclass(frozen=True)
class GateResult:
    """Outcome of one gate: pass/fail, the metric seen and the threshold."""

    gate: str
    passed: bool
    metric: float | None
    threshold: str
    reason: str


@dataclass(frozen=True)
class FortressVerdict:
    """All gate results plus headline metrics for one strategy series."""

    passed: bool
    gates: tuple[GateResult, ...]
    metrics: Mapping[str, float]

    def gate(self, name: str) -> GateResult:
        """Return the result of the named gate; raise KeyError if absent."""
        for result in self.gates:
            if result.gate == name:
                return result
        raise KeyError(name)

    def summary(self) -> str:
        """One-line human summary of the verdict."""
        failed = [result.gate for result in self.gates if not result.passed]
        status = "PASS" if self.passed else "FAIL"
        detail = "all gates passed" if not failed else "failed: " + ", ".join(failed)
        total = self.metrics.get("total_net_return", float("nan"))
        sharpe = self.metrics.get("annualized_sharpe", float("nan"))
        return (
            f"{status} validation fortress ({detail}); "
            f"total_net_return={total:.6g}, annualized_sharpe={sharpe:.6g}"
        )


def _thresholds(config: FortressConfig) -> dict[str, str]:
    return {
        "DATA_INTEGRITY": (
            "equal non-zero lengths; finite values; costs >= 0; net == gross - costs"
        ),
        "SAMPLE_SIZE": (
            f"trades >= {config.min_trades}, periods >= {config.min_periods}, "
            f"holdout periods >= {config.min_holdout_periods}"
        ),
        "EXPECTANCY": "total net > 0, mean net > 0, annualized Sharpe > 0",
        "WALK_FORWARD": (f"positive fold fraction >= {config.walk_forward_min_positive_fraction}"),
        "HOLDOUT": "holdout total > 0 and holdout Sharpe > 0",
        "CSCV_PBO": f"PBO <= {config.pbo_max}",
        "DEFLATED_SHARPE": f"deflated Sharpe >= {config.dsr_min}",
        "COST_STRESS_2X": "stressed (2x costs) total > 0 and mean > 0",
    }


def _failed(gate: str, threshold: str, reason: str, metric: float | None = None) -> GateResult:
    return GateResult(gate, False, metric, threshold, reason)


def _as_float_tuple(raw: object, label: str) -> tuple[float, ...]:
    """Convert one raw series to a float tuple or raise ValueError."""
    try:
        return tuple(float(value) for value in raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not a numeric series") from error


def _check_integrity(series: StrategySeries, threshold: str) -> GateResult:
    """Gate 1: structural and accounting checks; never raises."""
    gate = "DATA_INTEGRITY"
    try:
        net = _as_float_tuple(series.net_returns, "net_returns")
        gross = _as_float_tuple(series.gross_returns, "gross_returns")
        costs = _as_float_tuple(series.period_costs, "period_costs")
        if not isinstance(series.variant_net_returns, Mapping):
            return _failed(gate, threshold, "variant_net_returns is not a mapping")
        variants = {
            str(name): _as_float_tuple(values, f"variant {name!r}")
            for name, values in series.variant_net_returns.items()
        }
        trade_count = float(series.trade_count)
        periods_per_year = float(series.periods_per_year)
        n_trials = float(series.n_trials)
    except Exception as error:  # fail closed on any malformed input
        return _failed(gate, threshold, f"unreadable series: {error}")
    count = len(net)
    metric = float(count) if count else None
    if count == 0 or len(gross) != count or len(costs) != count:
        return _failed(
            gate,
            threshold,
            "net/gross/costs must share one non-zero length "
            f"(got {count}/{len(gross)}/{len(costs)})",
            metric,
        )
    for label, values in (("net", net), ("gross", gross), ("costs", costs)):
        if not all(math.isfinite(value) for value in values):
            return _failed(gate, threshold, f"non-finite value in {label} returns", metric)
    for name, values in variants.items():
        if len(values) != count:
            return _failed(
                gate,
                threshold,
                f"variant {name!r} has length {len(values)}, expected {count}",
                metric,
            )
        if not all(math.isfinite(value) for value in values):
            return _failed(gate, threshold, f"non-finite value in variant {name!r}", metric)
    if any(value < -1e-12 for value in costs):
        return _failed(gate, threshold, "period costs must be >= 0", metric)
    for index in range(count):
        tolerance = 1e-9 + 1e-6 * abs(gross[index])
        if abs(net[index] - (gross[index] - costs[index])) > tolerance:
            return _failed(
                gate,
                threshold,
                f"net != gross - costs at period {index}",
                metric,
            )
    if not math.isfinite(trade_count) or trade_count < 0:
        return _failed(gate, threshold, "trade_count must be >= 0", metric)
    if not math.isfinite(periods_per_year) or periods_per_year <= 0:
        return _failed(gate, threshold, "periods_per_year must be > 0", metric)
    if not math.isfinite(n_trials) or n_trials < 1:
        return _failed(gate, threshold, "n_trials must be >= 1", metric)
    return GateResult(gate, True, metric, threshold, "series is structurally sound")


def _per_period_sharpe(values: tuple[float, ...]) -> float:
    """Mean / sample std (ddof=1); zero std -> +inf if mean > 0 else 0."""
    if not values:
        return 0.0
    mean = statistics.fmean(values)
    if len(values) < 2:
        return math.inf if mean > 0 else 0.0
    deviation = statistics.stdev(values)
    if deviation == 0.0:
        return math.inf if mean > 0 else 0.0
    return mean / deviation


def _max_drawdown(values: tuple[float, ...]) -> float:
    """Largest peak-to-trough decline of the cumulative net sum (>= 0)."""
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _split_bounds(length: int, parts: int) -> list[tuple[int, int]]:
    """Contiguous [start, end) bounds; remainder goes to earliest parts."""
    base, remainder = divmod(length, parts)
    bounds = []
    start = 0
    for index in range(parts):
        size = base + (1 if index < remainder else 0)
        bounds.append((start, start + size))
        start += size
    return bounds


def _walk_forward(
    net: tuple[float, ...], config: FortressConfig, threshold: str
) -> tuple[GateResult, float]:
    gate = "WALK_FORWARD"
    folds = config.walk_forward_folds
    if folds < 1:
        return _failed(gate, threshold, "walk_forward_folds must be >= 1"), float("nan")
    if len(net) < folds:
        return (
            _failed(gate, threshold, f"fewer periods ({len(net)}) than folds ({folds})"),
            float("nan"),
        )
    totals = [sum(net[start:end]) for start, end in _split_bounds(len(net), folds)]
    fraction = sum(1 for total in totals if total > 0) / folds
    if fraction >= config.walk_forward_min_positive_fraction:
        return (
            GateResult(gate, True, fraction, threshold, f"{fraction:.3f} of folds profitable"),
            fraction,
        )
    return (
        _failed(gate, threshold, f"only {fraction:.3f} of folds profitable", metric=fraction),
        fraction,
    )


def _cscv_pbo(
    series: StrategySeries, count: int, config: FortressConfig, threshold: str
) -> tuple[GateResult, float]:
    gate = "CSCV_PBO"
    not_computable = float("nan")
    variants = list(series.variant_net_returns.values())
    if len(variants) < 2:
        return _failed(gate, threshold, "not computable: fewer than 2 variants"), not_computable
    blocks = config.cscv_blocks
    if blocks < 2 or blocks % 2 != 0:
        return (
            _failed(gate, threshold, "not computable: cscv_blocks must be even and >= 2"),
            not_computable,
        )
    if count < blocks:
        return (
            _failed(gate, threshold, "not computable: fewer periods than CSCV blocks"),
            not_computable,
        )
    bounds = _split_bounds(count, blocks)
    block_totals = [[sum(values[start:end]) for start, end in bounds] for values in variants]
    totals = [sum(block_values) for block_values in block_totals]
    median_rank = (len(variants) + 1) / 2
    overfit = 0
    combinations_seen = 0
    for in_sample in combinations(range(blocks), blocks // 2):
        combinations_seen += 1
        is_totals = [
            sum(block_values[block] for block in in_sample) for block_values in block_totals
        ]
        best = is_totals.index(max(is_totals))
        best_oos = totals[best] - is_totals[best]
        oos_totals = [total - is_total for total, is_total in zip(totals, is_totals, strict=True)]
        below = sum(1 for value in oos_totals if value < best_oos)
        tied = sum(1 for value in oos_totals if value == best_oos)
        rank = below + (tied + 1) / 2
        if rank <= median_rank:
            overfit += 1
    pbo = overfit / combinations_seen
    if pbo <= config.pbo_max:
        return GateResult(gate, True, pbo, threshold, f"PBO {pbo:.3f} within limit"), pbo
    return _failed(gate, threshold, f"PBO {pbo:.3f} above limit", metric=pbo), pbo


def _skew_kurtosis(values: tuple[float, ...]) -> tuple[float, float]:
    """Adjusted sample skewness and non-excess sample kurtosis."""
    count = len(values)
    mean = statistics.fmean(values)
    m2 = sum((value - mean) ** 2 for value in values) / count
    if m2 == 0.0:
        return 0.0, 3.0
    m3 = sum((value - mean) ** 3 for value in values) / count
    m4 = sum((value - mean) ** 4 for value in values) / count
    skew_raw = m3 / m2**1.5
    excess_raw = m4 / m2**2 - 3.0
    if count >= 4:
        skew = math.sqrt(count * (count - 1)) / (count - 2) * skew_raw
        excess = (count - 1) / ((count - 2) * (count - 3)) * ((count + 1) * excess_raw + 6.0)
        return skew, excess + 3.0
    return skew_raw, excess_raw + 3.0


def _trial_dispersion(series: StrategySeries) -> float | None:
    """Variance of trial Sharpes, or None when it cannot be estimated."""
    try:
        if len(series.trial_sharpes) >= 2:
            variance = statistics.variance(tuple(float(v) for v in series.trial_sharpes))
        elif len(series.variant_net_returns) >= 2:
            sharpes = [
                _per_period_sharpe(tuple(float(v) for v in values))
                for values in series.variant_net_returns.values()
            ]
            if not all(math.isfinite(sharpe) for sharpe in sharpes):
                return None
            variance = statistics.variance(sharpes)
        else:
            return None
    except Exception:  # fail closed: dispersion not estimable
        return None
    return variance if math.isfinite(variance) else None


def _deflated_sharpe(
    series: StrategySeries,
    net: tuple[float, ...],
    config: FortressConfig,
    threshold: str,
) -> tuple[GateResult, float]:
    gate = "DEFLATED_SHARPE"
    not_computable = float("nan")
    count = len(net)
    if count < 2:
        return _failed(gate, threshold, "not computable: fewer than 2 periods"), not_computable
    dispersion = _trial_dispersion(series)
    if dispersion is None:
        return (
            _failed(gate, threshold, "trial dispersion not estimable"),
            not_computable,
        )
    sharpe = _per_period_sharpe(net)
    if math.isinf(sharpe):
        dsr = 1.0 if sharpe > 0 else 0.0
        reason = "degenerate zero-variance series; DSR set by Sharpe sign"
        if dsr >= config.dsr_min:
            return GateResult(gate, True, dsr, threshold, reason), dsr
        return _failed(gate, threshold, reason, metric=dsr), dsr
    trials = int(series.n_trials)
    if trials <= 1:
        expected_max = 0.0
    else:
        normal = NormalDist()
        expected_max = math.sqrt(dispersion) * (
            (1.0 - EULER_GAMMA) * normal.inv_cdf(1.0 - 1.0 / trials)
            + EULER_GAMMA * normal.inv_cdf(1.0 - 1.0 / (trials * math.e))
        )
    skew, kurtosis = _skew_kurtosis(net)
    denominator = 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe**2
    if denominator <= 0.0 or not math.isfinite(denominator):
        dsr = 0.0
        return (
            _failed(gate, threshold, "non-positive DSR denominator; DSR set to 0", metric=dsr),
            dsr,
        )
    argument = (sharpe - expected_max) * math.sqrt(count - 1) / math.sqrt(denominator)
    dsr = NormalDist().cdf(argument)
    if dsr >= config.dsr_min:
        return GateResult(gate, True, dsr, threshold, f"DSR {dsr:.4f} clears minimum"), dsr
    return _failed(gate, threshold, f"DSR {dsr:.4f} below minimum", metric=dsr), dsr


def _fallback_metrics(series: StrategySeries) -> dict[str, float]:
    """Best-effort metrics for a series that failed integrity; never raises."""
    metrics = {key: float("nan") for key in _METRIC_KEYS}
    try:
        metrics["periods"] = float(len(series.net_returns))
    except Exception:
        pass
    try:
        metrics["trade_count"] = float(series.trade_count)
    except Exception:
        pass
    for key, attribute in (
        ("total_net_return", "net_returns"),
        ("total_gross_return", "gross_returns"),
        ("total_costs", "period_costs"),
    ):
        try:
            metrics[key] = float(sum(float(value) for value in getattr(series, attribute)))
        except Exception:
            pass
    return metrics


def _fallback_verdict(series: StrategySeries, error: Exception) -> FortressVerdict:
    reason = f"not evaluated: fortress failed closed on invalid input ({type(error).__name__})"
    gates = tuple(GateResult(name, False, None, "", reason) for name in _GATE_ORDER)
    try:
        metrics: Mapping[str, float] = _fallback_metrics(series)
    except Exception:
        metrics = {key: float("nan") for key in _METRIC_KEYS}
    return FortressVerdict(False, gates, metrics)


def _run_fortress(series: StrategySeries, config: FortressConfig) -> FortressVerdict:
    thresholds = _thresholds(config)
    integrity = _check_integrity(series, thresholds["DATA_INTEGRITY"])
    if not integrity.passed:
        gates = [integrity]
        gates.extend(
            _failed(name, thresholds[name], "not evaluated: data integrity failed")
            for name in _GATE_ORDER[1:]
        )
        return FortressVerdict(False, tuple(gates), _fallback_metrics(series))

    net = tuple(float(value) for value in series.net_returns)
    gross = tuple(float(value) for value in series.gross_returns)
    costs = tuple(float(value) for value in series.period_costs)
    count = len(net)
    holdout_length = min(count, max(0, math.ceil(config.holdout_fraction * count)))
    holdout = net[count - holdout_length :] if holdout_length else ()
    total_net = sum(net)
    mean_net = statistics.fmean(net)
    sharpe = _per_period_sharpe(net)
    annualized_sharpe = sharpe * math.sqrt(float(series.periods_per_year))
    holdout_total = sum(holdout)
    holdout_sharpe = _per_period_sharpe(holdout)
    stressed = tuple(g - 2.0 * c for g, c in zip(gross, costs, strict=True))
    stressed_total = sum(stressed)

    gates: list[GateResult] = [integrity]

    shortfalls = []
    if series.trade_count < config.min_trades:
        shortfalls.append(f"trades {series.trade_count} < {config.min_trades}")
    if count < config.min_periods:
        shortfalls.append(f"periods {count} < {config.min_periods}")
    if holdout_length < config.min_holdout_periods:
        shortfalls.append(f"holdout periods {holdout_length} < {config.min_holdout_periods}")
    if shortfalls:
        gates.append(
            _failed(
                "SAMPLE_SIZE",
                thresholds["SAMPLE_SIZE"],
                "; ".join(shortfalls),
                metric=float(count),
            )
        )
    else:
        gates.append(
            GateResult(
                "SAMPLE_SIZE",
                True,
                float(count),
                thresholds["SAMPLE_SIZE"],
                "sample size sufficient",
            )
        )

    expectancy_ok = total_net > 0 and mean_net > 0 and annualized_sharpe > 0
    gates.append(
        GateResult(
            "EXPECTANCY",
            expectancy_ok,
            annualized_sharpe,
            thresholds["EXPECTANCY"],
            "positive expectancy"
            if expectancy_ok
            else "total, mean and annualized Sharpe must all be positive",
        )
    )

    walk_forward, walk_forward_fraction = _walk_forward(net, config, thresholds["WALK_FORWARD"])
    gates.append(walk_forward)

    holdout_ok = bool(holdout) and holdout_total > 0 and holdout_sharpe > 0
    gates.append(
        GateResult(
            "HOLDOUT",
            holdout_ok,
            holdout_sharpe if holdout else None,
            thresholds["HOLDOUT"],
            "holdout profitable"
            if holdout_ok
            else "holdout total and Sharpe must both be positive",
        )
    )

    cscv, pbo = _cscv_pbo(series, count, config, thresholds["CSCV_PBO"])
    gates.append(cscv)

    dsr_gate, dsr = _deflated_sharpe(series, net, config, thresholds["DEFLATED_SHARPE"])
    gates.append(dsr_gate)

    stressed_ok = stressed_total > 0 and statistics.fmean(stressed) > 0
    gates.append(
        GateResult(
            "COST_STRESS_2X",
            stressed_ok,
            stressed_total,
            thresholds["COST_STRESS_2X"],
            "profitable at 2x costs" if stressed_ok else "doubling costs wipes out the edge",
        )
    )

    metrics: dict[str, float] = {
        "total_net_return": float(total_net),
        "total_gross_return": float(sum(gross)),
        "total_costs": float(sum(costs)),
        "mean_period_return": float(mean_net),
        "annualized_sharpe": float(annualized_sharpe),
        "max_drawdown": float(_max_drawdown(net)),
        "holdout_total_return": float(holdout_total),
        "holdout_sharpe": float(holdout_sharpe),
        "walk_forward_positive_fraction": float(walk_forward_fraction),
        "pbo": float(pbo),
        "deflated_sharpe": float(dsr),
        "stressed_total_return": float(stressed_total),
        "trade_count": float(series.trade_count),
        "periods": float(count),
    }
    return FortressVerdict(all(gate.passed for gate in gates), tuple(gates), metrics)


def run_fortress(
    series: StrategySeries, config: FortressConfig = FortressConfig()
) -> FortressVerdict:
    """Evaluate every Validation Fortress gate against ``series``.

    Fail-closed: this function never raises on bad or degenerate data;
    every problem is reported as a failed gate with a reason.
    """
    try:
        return _run_fortress(series, config)
    except Exception as error:  # fail closed on any unexpected input shape
        return _fallback_verdict(series, error)

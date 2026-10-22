"""Edge Sprint hypothesis library: pre-registered, falsifiable FX hypotheses.

Signal semantics (the contract every runner must rely on)
---------------------------------------------------------
``signal(bars, params, carry_diff)`` returns target positions with exactly
``len(bars)`` entries. ``positions[t]`` is decided using only data up to and
including bar ``t`` (no lookahead) and is held over the interval ``(t, t+1]``.
Every value lies in ``[-1.0, 1.0]``: ``+1`` is fully long the pair, ``-1`` is
fully short, ``0`` is flat.

``carry_diff`` is ``None`` unless the hypothesis has ``uses_carry=True``. When
provided it is the annual interest-rate differential (base-currency rate minus
quote-currency rate, as a decimal such as ``0.045``) aligned with ``bars``;
``carry_diff[t]`` is assumed known at time ``t``. Carry hypotheses fail safe:
they return all-zero positions when ``carry_diff`` is ``None``.

How each family avoids lookahead
--------------------------------
Session/volatility (hourly): bars are grouped by UTC calendar date. The Asia
range uses only bars starting 00:00-06:59 UTC, which are complete before the
07:00 London window opens, and a breakout fills positions only from the
breakout bar forward -- a day's stance is never back-filled. London-move
signals (S03/S04) derive their direction from bars that end before the holding
window starts. S05 evaluates a spike on bar ``t`` but only takes the fade from
bar ``t+1``. S08 compares Monday's first open with the previous (Friday)
close and an ATR that ends on that Friday bar. S06 compares today's Asia
width only with the widths of strictly earlier days.

Carry/momentum (daily): moving averages, momentum and realised volatility at
``t`` use closes up to and including ``t`` only (prefix sums). Donchian
extremes exclude the current bar. The volatility median in M06 is taken over
realised-volatility values up to and including ``t``.

Efficiency: all rolling means/ATR/volatility use prefix sums, rolling
extremes use monotonic deques, and the rolling median uses a sorted list of
at most ``vol_median_days`` entries, so every signal is O(n * small window)
or better for ~37k hourly bars. Standard library only.
"""

import bisect
import math
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType

_ALL_INSTRUMENTS = ("EURUSD", "GBPUSD", "USDJPY", "EURJPY")

_FALSIFICATION = (
    "Rejected if the Validation Fortress verdict is FAIL on the sprint dataset, "
    "or if net expectancy after 2x costs <= 0. No re-tuning under this ID."
)


@dataclass(frozen=True)
class BarsView:
    """OHLC bars for one instrument; timestamps tz-aware UTC, strictly increasing."""

    instrument_id: str
    timestamps: tuple[datetime, ...]
    opens: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    closes: tuple[float, ...]


SignalFn = Callable[[BarsView, Mapping[str, float], "tuple[float, ...] | None"], tuple[float, ...]]


@dataclass(frozen=True)
class Hypothesis:
    """One pre-registered hypothesis with its parameter neighbourhood."""

    id: str
    family: str
    name: str
    instruments: tuple[str, ...]
    timeframe: str
    mechanism: str
    falsifiable_claim: str
    failure_regime: str
    falsification_criterion: str
    base_params: Mapping[str, float]
    variants: tuple[Mapping[str, float], ...]
    uses_carry: bool
    signal: SignalFn


# ---------------------------------------------------------------------------
# Small numeric helpers (prefix sums / rolling statistics, no lookahead)
# ---------------------------------------------------------------------------


def _param(params: Mapping[str, float], name: str, default: float) -> float:
    return float(params.get(name, default))


def _int_param(params: Mapping[str, float], name: str, default: float) -> int:
    return max(1, int(round(_param(params, name, default))))


def _prefix_sums(values: tuple[float, ...] | list[float]) -> list[float]:
    prefix = [0.0] * (len(values) + 1)
    total = 0.0
    for index, value in enumerate(values):
        total += value
        prefix[index + 1] = total
    return prefix


def _window_mean(prefix: list[float], end: int, window: int) -> float | None:
    """Mean of values[end - window + 1 .. end]; None until the window is full."""
    if end + 1 < window:
        return None
    return (prefix[end + 1] - prefix[end + 1 - window]) / window


def _true_ranges(bars: BarsView) -> list[float]:
    n = len(bars.closes)
    ranges = [0.0] * n
    for t in range(n):
        high, low = bars.highs[t], bars.lows[t]
        if t == 0:
            ranges[t] = high - low
        else:
            prev_close = bars.closes[t - 1]
            ranges[t] = max(high - low, abs(high - prev_close), abs(low - prev_close))
    return ranges


def _atr_series(bars: BarsView, window: int) -> list[float | None]:
    """ATR(window) at each t: mean true range over the window ending at t."""
    prefix = _prefix_sums(_true_ranges(bars))
    return [_window_mean(prefix, t, window) for t in range(len(bars.closes))]


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation quantile of an already sorted, non-empty list."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _rolling_extreme_excluding_current(
    values: tuple[float, ...], window: int, want_max: bool
) -> list[float | None]:
    """Extreme of values[t - window .. t - 1] at each t (None until t >= window)."""
    n = len(values)
    result: list[float | None] = [None] * n
    candidates: deque[int] = deque()
    for t in range(n):
        while candidates and candidates[0] < t - window:
            candidates.popleft()
        if t >= window and candidates:
            result[t] = values[candidates[0]]
        # Add bar t so it is available to future bars (never to itself).
        value = values[t]
        if want_max:
            while candidates and values[candidates[-1]] <= value:
                candidates.pop()
        else:
            while candidates and values[candidates[-1]] >= value:
                candidates.pop()
        candidates.append(t)
    return result


def _day_groups(bars: BarsView) -> list[tuple[date, list[int]]]:
    """Indices grouped by UTC calendar date, in chronological order."""
    groups: list[tuple[date, list[int]]] = []
    for index, stamp in enumerate(bars.timestamps):
        day = stamp.date()
        if groups and groups[-1][0] == day:
            groups[-1][1].append(index)
        else:
            groups.append((day, [index]))
    return groups


def _asia_range(bars: BarsView, indices: list[int]) -> tuple[float, float] | None:
    """(high, low) of bars starting 00:00-06:59 UTC; None if no such bars exist."""
    asia = [i for i in indices if bars.timestamps[i].hour <= 6]
    if not asia:
        return None
    high = max(bars.highs[i] for i in asia)
    low = min(bars.lows[i] for i in asia)
    return high, low


def _sign(value: float) -> float:
    if value > 0:
        return 1.0
    if value < 0:
        return -1.0
    return 0.0


# ---------------------------------------------------------------------------
# Session / volatility signals (timeframe "1h")
# ---------------------------------------------------------------------------


def _asia_breakout_signal(
    bars: BarsView,
    params: Mapping[str, float],
    fade: bool,
    allowed_days: set[date] | None,
) -> tuple[float, ...]:
    """Shared S01/S02/S06 engine: first London close beyond the Asia range.

    The breakout trigger is the range edge widened by ``buffer_frac`` of the
    range width (so ``buffer_frac`` is a fraction of the width, as registered).
    The stance starts on the breakout bar, is held through the 20:00 bar, and
    is flat from 21:00 UTC. Only bars starting 07:00-16:59 UTC can trigger.
    In fade mode the stance is opposite the breakout and additionally exits
    on the first close back to the range midpoint.
    """
    n = len(bars.closes)
    positions = [0.0] * n
    buffer_frac = _param(params, "buffer_frac", 0.10)
    for day, indices in _day_groups(bars):
        if allowed_days is not None and day not in allowed_days:
            continue
        asia = _asia_range(bars, indices)
        if asia is None:
            continue
        range_high, range_low = asia
        width = range_high - range_low
        if width <= 0:
            continue
        long_trigger = range_high + buffer_frac * width
        short_trigger = range_low - buffer_frac * width
        midpoint = 0.5 * (range_high + range_low)
        stance = 0.0
        breakout_direction = 0.0
        exited = False
        for i in indices:
            hour = bars.timestamps[i].hour
            if hour < 7 or hour >= 21 or exited:
                continue
            if stance == 0.0:
                if hour > 16:
                    continue
                close = bars.closes[i]
                if close > long_trigger:
                    breakout_direction = 1.0
                elif close < short_trigger:
                    breakout_direction = -1.0
                else:
                    continue
                stance = -breakout_direction if fade else breakout_direction
                positions[i] = stance
            else:
                if fade:
                    close = bars.closes[i]
                    hit_midpoint = (breakout_direction > 0 and close <= midpoint) or (
                        breakout_direction < 0 and close >= midpoint
                    )
                    if hit_midpoint:
                        positions[i] = 0.0
                        exited = True
                        stance = 0.0
                        continue
                positions[i] = stance
    return tuple(positions)


def _s01_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    return _asia_breakout_signal(bars, params, fade=False, allowed_days=None)


def _s02_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    return _asia_breakout_signal(bars, params, fade=True, allowed_days=None)


def _s06_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    lookback = _int_param(params, "lookback_days", 20.0)
    quantile = _param(params, "quantile", 0.30)
    widths: list[float] = []
    allowed: set[date] = set()
    for day, indices in _day_groups(bars):
        asia = _asia_range(bars, indices)
        if asia is None:
            continue
        width = asia[0] - asia[1]
        if len(widths) >= lookback:
            trailing = sorted(widths[-lookback:])
            if width < _quantile(trailing, quantile):
                allowed.add(day)
        widths.append(width)
    return _asia_breakout_signal(bars, params, fade=False, allowed_days=allowed)


def _s03_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    start_hour = _int_param(params, "hold_start_hour", 8.0)
    end_hour = _int_param(params, "hold_end_hour", 16.0)
    for _day, indices in _day_groups(bars):
        move_index = next((i for i in indices if bars.timestamps[i].hour == 7), None)
        if move_index is None:
            continue
        # The 07:00 bar ends at 08:00; its open-to-close move sets the stance.
        direction = _sign(bars.closes[move_index] - bars.opens[move_index])
        if direction == 0.0:
            continue
        for i in indices:
            if start_hour <= bars.timestamps[i].hour < end_hour:
                positions[i] = direction
    return tuple(positions)


def _s04_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    k = _param(params, "k", 1.0)
    atr = _atr_series(bars, 14)
    for _day, indices in _day_groups(bars):
        start_index = next((i for i in indices if bars.timestamps[i].hour == 7), None)
        # The 11:00 bar ends at 12:00; the move is open(07:00) -> close(11:00 bar).
        end_index = next((i for i in indices if bars.timestamps[i].hour == 11), None)
        if start_index is None or end_index is None:
            continue
        atr_at_noon = atr[end_index]
        if atr_at_noon is None or atr_at_noon <= 0:
            continue
        move = bars.closes[end_index] - bars.opens[start_index]
        if move == 0.0 or abs(move) <= k * atr_at_noon:
            continue
        fade = -_sign(move)
        for i in indices:
            if 13 <= bars.timestamps[i].hour < 20:
                positions[i] = fade
    return tuple(positions)


def _s05_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    spike_mult = _param(params, "spike_mult", 2.0)
    third = _param(params, "extreme_third", 1.0 / 3.0)
    hold_bars = _int_param(params, "hold_bars", 3.0)
    true_ranges = _true_ranges(bars)
    atr_prefix = _prefix_sums(true_ranges)
    active_direction = 0.0
    remaining = 0
    for t in range(n):
        if remaining > 0:
            positions[t] = active_direction
            remaining -= 1
            if remaining == 0:
                active_direction = 0.0
        # Evaluate the spike on bar t; any fade starts on bar t + 1 only.
        # The benchmark ATR(20) ends at t - 1 so the spike is not in its own base.
        prior_atr = _window_mean(atr_prefix, t - 1, 20) if t >= 1 else None
        if prior_atr is None or prior_atr <= 0:
            continue
        bar_range = bars.highs[t] - bars.lows[t]
        if bar_range <= 0 or true_ranges[t] <= spike_mult * prior_atr:
            continue
        close_fraction = (bars.closes[t] - bars.lows[t]) / bar_range
        if close_fraction >= 1.0 - third:
            active_direction = -1.0
        elif close_fraction <= third:
            active_direction = 1.0
        else:
            continue
        remaining = hold_bars
    return tuple(positions)


def _s07_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    start_hour = _int_param(params, "start_hour", 7.0)
    end_hour = _int_param(params, "end_hour", 10.0)
    positions = [0.0] * len(bars.closes)
    for i, stamp in enumerate(bars.timestamps):
        if stamp.weekday() < 5 and start_hour <= stamp.hour < end_hour:
            positions[i] = 1.0
    return tuple(positions)


def _s08_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    gap_atr = _param(params, "gap_atr", 1.0)
    atr = _atr_series(bars, 14)
    for day, indices in _day_groups(bars):
        if day.weekday() != 0:
            continue
        first = indices[0]
        if first == 0:
            continue
        previous = first - 1
        previous_day = bars.timestamps[previous].date()
        if previous_day.weekday() != 4 or not 1 <= (day - previous_day).days <= 4:
            continue
        atr_friday = atr[previous]
        if atr_friday is None or atr_friday <= 0:
            continue
        gap = bars.opens[first] - bars.closes[previous]
        if gap == 0.0 or abs(gap) <= gap_atr * atr_friday:
            continue
        fade = -_sign(gap)
        for i in indices:
            if bars.timestamps[i].hour < 12:
                positions[i] = fade
    return tuple(positions)


# ---------------------------------------------------------------------------
# Carry / momentum signals (timeframe "1d")
# ---------------------------------------------------------------------------


def _sma_series(values: tuple[float, ...], window: int) -> list[float | None]:
    prefix = _prefix_sums(values)
    return [_window_mean(prefix, t, window) for t in range(len(values))]


def _momentum_sign(bars: BarsView, t: int, lookback: int) -> float:
    if t < lookback or bars.closes[t - lookback] <= 0:
        return 0.0
    return _sign(bars.closes[t] / bars.closes[t - lookback] - 1.0)


def _m01_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    lookback = _int_param(params, "lookback_days", 60.0)
    return tuple(_momentum_sign(bars, t, lookback) for t in range(len(bars.closes)))


def _m02_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    fast = _int_param(params, "fast_days", 20.0)
    slow = _int_param(params, "slow_days", 100.0)
    fast_sma = _sma_series(bars.closes, fast)
    slow_sma = _sma_series(bars.closes, slow)
    positions = [0.0] * len(bars.closes)
    for t in range(len(bars.closes)):
        fast_value, slow_value = fast_sma[t], slow_sma[t]
        if fast_value is not None and slow_value is not None:
            positions[t] = _sign(fast_value - slow_value)
    return tuple(positions)


def _m03_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    entry_days = _int_param(params, "entry_days", 55.0)
    exit_days = _int_param(params, "exit_days", 20.0)
    entry_high = _rolling_extreme_excluding_current(bars.highs, entry_days, want_max=True)
    entry_low = _rolling_extreme_excluding_current(bars.lows, entry_days, want_max=False)
    exit_high = _rolling_extreme_excluding_current(bars.highs, exit_days, want_max=True)
    exit_low = _rolling_extreme_excluding_current(bars.lows, exit_days, want_max=False)
    positions = [0.0] * len(bars.closes)
    state = 0.0
    for t, close in enumerate(bars.closes):
        if state > 0 and exit_low[t] is not None and close < exit_low[t]:
            state = 0.0
        elif state < 0 and exit_high[t] is not None and close > exit_high[t]:
            state = 0.0
        if state == 0.0 and entry_high[t] is not None and entry_low[t] is not None:
            if close > entry_high[t]:
                state = 1.0
            elif close < entry_low[t]:
                state = -1.0
        positions[t] = state
    return tuple(positions)


def _carry_direction(value: float, band: float) -> float:
    if value > band:
        return 1.0
    if value < -band:
        return -1.0
    return 0.0


def _m04_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    if carry_diff is None:
        return (0.0,) * n
    band = _param(params, "band", 0.0025)
    return tuple(
        _carry_direction(carry_diff[t], band) if t < len(carry_diff) else 0.0 for t in range(n)
    )


def _m05_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    if carry_diff is None:
        return (0.0,) * n
    band = _param(params, "band", 0.0010)
    lookback = _int_param(params, "lookback_days", 60.0)
    positions = [0.0] * n
    for t in range(n):
        if t >= len(carry_diff):
            break
        carry = _carry_direction(carry_diff[t], band)
        if carry != 0.0 and carry == _momentum_sign(bars, t, lookback):
            positions[t] = carry
    return tuple(positions)


def _realised_vol_series(bars: BarsView, window: int) -> list[float | None]:
    """Population stdev of the last ``window`` daily simple returns, per bar."""
    n = len(bars.closes)
    returns = [0.0] * n
    for t in range(1, n):
        if bars.closes[t - 1] > 0:
            returns[t] = bars.closes[t] / bars.closes[t - 1] - 1.0
    sum_prefix = _prefix_sums(returns)
    square_prefix = _prefix_sums([value * value for value in returns])
    result: list[float | None] = [None] * n
    for t in range(n):
        mean = _window_mean(sum_prefix, t, window)
        if mean is None:
            continue
        mean_square = _window_mean(square_prefix, t, window)
        if mean_square is None:
            continue
        result[t] = math.sqrt(max(mean_square - mean * mean, 0.0))
    return result


def _m06_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    lookback = _int_param(params, "lookback_days", 60.0)
    vol_window = _int_param(params, "vol_window_days", 20.0)
    vol_mult = _param(params, "vol_mult", 1.5)
    median_days = _int_param(params, "vol_median_days", 252.0)
    volatility = _realised_vol_series(bars, vol_window)
    sorted_window: list[float] = []
    for t in range(n):
        current = volatility[t]
        if current is not None:
            bisect.insort(sorted_window, current)
        outgoing_index = t - median_days
        if 0 <= outgoing_index and volatility[outgoing_index] is not None:
            outgoing = volatility[outgoing_index]
            del sorted_window[bisect.bisect_left(sorted_window, outgoing)]
        if current is None or len(sorted_window) < median_days:
            continue
        middle = len(sorted_window) // 2
        if len(sorted_window) % 2:
            median = sorted_window[middle]
        else:
            median = 0.5 * (sorted_window[middle - 1] + sorted_window[middle])
        if current > vol_mult * median:
            continue
        positions[t] = _momentum_sign(bars, t, lookback)
    return tuple(positions)


# ---------------------------------------------------------------------------
# Hypothesis registry
# ---------------------------------------------------------------------------


def _scaled_variants(
    base: Mapping[str, float], factors: tuple[float, ...] = (0.75, 0.85, 1.0, 1.2, 1.3)
) -> tuple[Mapping[str, float], ...]:
    """Parameter neighbourhood: base scaled by each factor (1.0 reproduces base)."""
    return tuple(
        MappingProxyType({name: value * factor for name, value in base.items()})
        for factor in factors
    )


def _manual_variants(
    entries: tuple[dict[str, float], ...],
) -> tuple[Mapping[str, float], ...]:
    return tuple(MappingProxyType(entry) for entry in entries)


def _hypothesis(
    hypothesis_id: str,
    family: str,
    name: str,
    timeframe: str,
    mechanism: str,
    claim: str,
    failure_regime: str,
    base_params: dict[str, float],
    signal: SignalFn,
    uses_carry: bool = False,
    variants: tuple[Mapping[str, float], ...] | None = None,
) -> Hypothesis:
    base = MappingProxyType(dict(base_params))
    return Hypothesis(
        id=hypothesis_id,
        family=family,
        name=name,
        instruments=_ALL_INSTRUMENTS,
        timeframe=timeframe,
        mechanism=mechanism,
        falsifiable_claim=claim,
        failure_regime=failure_regime,
        falsification_criterion=_FALSIFICATION,
        base_params=base,
        variants=variants if variants is not None else _scaled_variants(base),
        uses_carry=uses_carry,
        signal=signal,
    )


_SESSION = "session_volatility"
_CARRY_MOMENTUM = "carry_momentum"

HYPOTHESES: tuple[Hypothesis, ...] = (
    _hypothesis(
        "S01",
        _SESSION,
        "AsiaRangeLondonBreakout",
        "1h",
        "Asia trades thin, range-bound flow; at the London open, institutional "
        "orders and stop clusters beyond the Asia range force one-sided hedging "
        "by dealers and late momentum entrants on the other side.",
        "On breakout days the stance earns a mean return of at least +0.15 x "
        "the Asia range width per trade net of costs, with a win rate of at "
        "least 45% across the four pairs.",
        "Range-expansion days driven by scheduled news, where the first break "
        "is an information move that whipsaws, and quiet holiday sessions.",
        {"buffer_frac": 0.10},
        _s01_signal,
    ),
    _hypothesis(
        "S02",
        _SESSION,
        "AsiaRangeLondonFade",
        "1h",
        "Early London breaks often sweep stop orders placed just beyond the "
        "Asia range; once the stops are filled there is no follow-through flow, "
        "and liquidity providers who absorbed the sweep sell the move back.",
        "Fading the first break earns at least +0.10 x the Asia range width "
        "per trade net of costs, reaching the range midpoint on at least 50% "
        "of breakout days.",
        "Genuine trend days with fresh fundamental news at the London open, "
        "where the breakout is the start of a one-way session.",
        {"buffer_frac": 0.10},
        _s02_signal,
    ),
    _hypothesis(
        "S03",
        _SESSION,
        "LondonOpenMomentum",
        "1h",
        "The first London hour aggregates overnight information and large "
        "institutional execution programmes; under-reaction and benchmark "
        "order flow keep pushing price the same way through the morning.",
        "The 08:00-16:00 stance in the direction of the 07:00 hour earns a "
        "mean of at least +2 bp per day net of costs, positive on at least "
        "52% of days.",
        "Days dominated by a single data release after 08:00 that reverses "
        "the opening move, and low-liquidity holiday sessions.",
        {"hold_start_hour": 8.0, "hold_end_hour": 16.0},
        _s03_signal,
        variants=_manual_variants(
            (
                {"hold_start_hour": 8.0, "hold_end_hour": 16.0},
                {"hold_start_hour": 8.0, "hold_end_hour": 15.0},
                {"hold_start_hour": 8.0, "hold_end_hour": 17.0},
                {"hold_start_hour": 9.0, "hold_end_hour": 16.0},
                {"hold_start_hour": 8.0, "hold_end_hour": 14.0},
            )
        ),
    ),
    _hypothesis(
        "S04",
        _SESSION,
        "LondonMoveNewYorkReversal",
        "1h",
        "An outsized London morning move is often positioning-driven rather "
        "than information-driven; New York entrants and profit-taking London "
        "desks on the other side fade the overextension in the afternoon.",
        "When the morning move exceeds 1.0 x ATR(14), the 13:00-20:00 fade "
        "earns a mean of at least +0.25 x ATR(14) per signal day net of "
        "costs, on at least 30 signal days per pair.",
        "Trend days where the morning move is the first leg of a major news "
        "repricing that continues through New York.",
        {"k": 1.0},
        _s04_signal,
    ),
    _hypothesis(
        "S05",
        _SESSION,
        "VolatilitySpikePullbackFade",
        "1h",
        "A single bar with more than double the normal range closing at its "
        "extreme is typically a stop cascade or a fat-finger liquidity vacuum; "
        "market makers who filled the spike re-price back toward fair value.",
        "The fade held for 3 bars earns a mean of at least +0.20 x ATR(20) "
        "per event net of costs, across at least 100 events per pair.",
        "Spikes caused by central-bank surprises or data shocks, where the "
        "extreme close is the new fair value and follow-through continues.",
        {"spike_mult": 2.0, "extreme_third": 1.0 / 3.0, "hold_bars": 3.0},
        _s05_signal,
    ),
    _hypothesis(
        "S06",
        _SESSION,
        "AsiaCompressionBreakout",
        "1h",
        "An unusually compressed Asia range means stops and breakout orders "
        "are packed tightly on both sides; London flow needs little size to "
        "trigger a cascade, so compressed days break more cleanly (S01 effect, "
        "conditioned on compression).",
        "On days in the bottom 30% of trailing Asia widths, the breakout "
        "stance earns at least +0.20 x the Asia range width per trade net of "
        "costs, beating the unconditional S01 expectancy by at least 25%.",
        "Compression caused by a known upcoming release, where London waits "
        "and the eventual break is a two-sided whipsaw.",
        {"buffer_frac": 0.10, "quantile": 0.30, "lookback_days": 20.0},
        _s06_signal,
    ),
    _hypothesis(
        "S07",
        _SESSION,
        "SessionDriftLondonMorning",
        "1h",
        "Structural morning flows (European corporate hedging, fund benchmark "
        "execution and dealer inventory recycling) create a persistent "
        "time-of-day drift in the London morning, identical across pairs.",
        "Being long 07:00-10:00 UTC every weekday earns a mean of at least "
        "+0.5 bp per hour held net of costs, with a positive mean in at "
        "least 3 of the 4 pairs.",
        "Periods of sustained risk-off dollar demand or yen strength that "
        "invert the habitual morning flow for months at a time.",
        {"start_hour": 7.0, "end_hour": 10.0},
        _s07_signal,
        variants=_manual_variants(
            (
                {"start_hour": 7.0, "end_hour": 10.0},
                {"start_hour": 7.0, "end_hour": 11.0},
                {"start_hour": 7.0, "end_hour": 9.0},
                {"start_hour": 6.0, "end_hour": 10.0},
                {"start_hour": 8.0, "end_hour": 10.0},
            )
        ),
    ),
    _hypothesis(
        "S08",
        _SESSION,
        "WeekendGapFade",
        "1h",
        "Weekend gaps are usually headline-driven overreaction priced in thin "
        "Sunday/Monday-open liquidity; dealers who quoted the gap wide expect "
        "retracement once normal London liquidity returns.",
        "Fading gaps larger than 1.0 x ATR(14) until Monday 12:00 UTC earns "
        "a mean of at least +0.25 x the gap size per event net of costs, "
        "across at least 40 gap events in total.",
        "Gaps caused by genuine regime events (elections, central-bank "
        "emergency action), where Monday continues in the gap direction.",
        {"gap_atr": 1.0},
        _s08_signal,
    ),
    _hypothesis(
        "M01",
        _CARRY_MOMENTUM,
        "TimeSeriesMomentum",
        "1d",
        "Slow-moving capital, hedging programmes and gradual information "
        "diffusion make FX trends persist for months; the other side is "
        "mean-reversion flow from exporters and reserve managers.",
        "The sign of the trailing 60-day return, held daily, delivers a "
        "net-of-cost Sharpe of at least 0.5 and a mean daily return of at "
        "least +1 bp across the four pairs.",
        "Choppy range-bound years with repeated central-bank regime flips, "
        "where 60-day trends reverse before they can be harvested.",
        {"lookback_days": 60.0},
        _m01_signal,
    ),
    _hypothesis(
        "M02",
        _CARRY_MOMENTUM,
        "MovingAverageCrossTrend",
        "1d",
        "A slow moving-average regime filter proxies the same institutional "
        "trend flow as M01 but stays invested through shallow pullbacks; the "
        "other side is short-horizon contrarian liquidity provision.",
        "Being long when SMA(20) > SMA(100) and short otherwise delivers a "
        "net-of-cost Sharpe of at least 0.4 and positive expectancy in at "
        "least 3 of the 4 pairs.",
        "Sideways markets where price crosses the slow average repeatedly, "
        "bleeding spread and slippage on whipsaw flips.",
        {"fast_days": 20.0, "slow_days": 100.0},
        _m02_signal,
    ),
    _hypothesis(
        "M03",
        _CARRY_MOMENTUM,
        "DonchianBreakout",
        "1d",
        "A close beyond the 55-day extreme forces systematic trend funds and "
        "option-barrier hedgers to trade in the breakout direction; stops of "
        "the losing side add fuel, which the breakout trader harvests.",
        "The turtle-style state machine delivers a net-of-cost profit factor "
        "of at least 1.3, with at least 25 completed trades per pair and a "
        "positive mean return per trade.",
        "False-breakout regimes in compressed ranges, where price repeatedly "
        "pokes to new extremes and immediately falls back.",
        {"entry_days": 55.0, "exit_days": 20.0},
        _m03_signal,
    ),
    _hypothesis(
        "M04",
        _CARRY_MOMENTUM,
        "CarryDifferential",
        "1d",
        "Yield-seeking capital persistently buys the higher-rate currency; "
        "the other side is trade-flow and hedging demand that is insensitive "
        "to rates, so the differential is paid to whoever holds the position.",
        "Holding the sign of the rate differential beyond a 0.25% band earns "
        "the differential plus a net-of-cost total return of at least +1.0% "
        "annualised, with a Sharpe of at least 0.4.",
        "Risk-off crises where high-yield currencies crash together and years "
        "of carry are repaid in weeks (carry unwind regime).",
        {"band": 0.0025},
        _m04_signal,
        uses_carry=True,
    ),
    _hypothesis(
        "M05",
        _CARRY_MOMENTUM,
        "CarryMomentumAgreement",
        "1d",
        "Carry performs worst exactly when momentum turns against it; "
        "requiring 60-day momentum to agree filters out the unwind phase "
        "while keeping the yield flow of aligned periods.",
        "The agreement-gated carry position beats ungated M04 by at least "
        "0.2 Sharpe net of costs and keeps a net expectancy above 0, while "
        "being invested on at least 30% of days.",
        "Sharp V-shaped reversals where momentum flips only after the carry "
        "loss has already happened, so the gate exits at the bottom.",
        {"band": 0.0010, "lookback_days": 60.0},
        _m05_signal,
        uses_carry=True,
    ),
    _hypothesis(
        "M06",
        _CARRY_MOMENTUM,
        "VolatilityManagedMomentum",
        "1d",
        "Momentum crashes cluster in volatility spikes, when leveraged trend "
        "followers are forced to cut into falling liquidity; standing aside "
        "when realised volatility is far above its norm avoids paying that "
        "forced flow.",
        "60-day momentum with the volatility gate improves on ungated M01 by "
        "at least 0.15 Sharpe net of costs, with a maximum drawdown at least "
        "20% smaller, and is flat on no more than 40% of days.",
        "Volatility spikes that mark the start of strong new trends, where "
        "the gate keeps the strategy flat through the most profitable leg.",
        {
            "lookback_days": 60.0,
            "vol_window_days": 20.0,
            "vol_mult": 1.5,
            "vol_median_days": 252.0,
        },
        _m06_signal,
    ),
)

_BY_ID = {hypothesis.id: hypothesis for hypothesis in HYPOTHESES}


def get_hypothesis(hypothesis_id: str) -> Hypothesis:
    """Return the registered hypothesis with this id; raises KeyError if absent."""
    return _BY_ID[hypothesis_id]

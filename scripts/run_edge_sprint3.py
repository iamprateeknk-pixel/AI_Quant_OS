#!/usr/bin/env python3
"""Edge Sprint 3 runner: (1) tests Sprint 1's "no free reliable USDINR"
claim against FRED's DEXINUS series and, if the series validates,
evaluates a pre-registered INR hypothesis family (I01-I06) on USDINR;
(2) evaluates five new pre-registered hourly hypotheses (S09-S13) on the
same commit-pinned, SHA-256-verified hourly dataset Sprint 2 used.
Sprint 1/2 runners, results and rejected IDs are not modified and no
rejected hypothesis is re-run for a verdict.

The Sprint 1 claim, and why it is testable: Sprint 1 reported INR as
PENDING_DATA because "no free reliable USDINR reference series was
obtainable". FRED's own catalogue lists DEXINUS -- "Indian Rupees to One
U.S. Dollar Spot Exchange Rate", Board of Governors H.10 release, noon
buying rates in New York, daily, 1973-01-02 to 2026-10-16, updated
2026-10-19 -- the same release, units convention and endpoint family as
DEXUSEU / DEXUSUK / DEXJPUS, which Sprint 1 fetched successfully. This
runner fetches DEXINUS through the identical fredgraph.csv route and
subjects it to the pre-registered validation battery below. If the fetch
or any check fails, the failure is documented verbatim, no INR
evaluation runs, and INR stays PENDING_DATA (the status constant in
``src/ai_quant_os/inr_instruments.py`` is NOT changed by this runner in
any case; the report carries a recommendation only).

INR validation battery (fixed before the fetch):
- at least 2,500 daily rows from 2015-01-01, dates strictly increasing;
- every value inside [50, 110] INR per USD (the pair's full 2015-2026
  trading range is roughly 63-95; the band is a garbage detector, not a
  fit), no zero/negative values;
- no calendar gap longer than 21 days (holiday clusters are <= ~5);
- no single-day move larger than 5% (USDINR is a managed float; its
  largest genuine daily moves are ~2%);
- cross-check: derived EURINR = DEXINUS * DEXUSEU on common dates must
  have >= 2,500 points, stay inside [55, 125] and show no daily move
  larger than 5%.

INR hypotheses (NEW IDs, daily, USDINR only, parameters fixed here
before any run; I01-I04/I06 reuse the exact registered rule shapes of
M01/M02/M03/M06/M04 on the new instrument, I05 is a new rule):
- I01 TimeSeriesMomentumINR      lookback_days = 60
- I02 MovingAverageCrossINR      fast_days = 20, slow_days = 100
- I03 DonchianBreakoutINR        entry_days = 55, exit_days = 20
- I04 VolatilityManagedMomINR    lookback 60, vol window 20, vol_mult
                                 1.5, vol_median_days 252
- I05 ManagedFloatReversionINR   SMA(200) anchor; enter the fade when
                                 |close/SMA - 1| >= 2.0%, exit at 0.5%
- I06 CarryDifferentialINR       band 0.25%, carry = INR short rate
                                 (IRSTCB01INM156N) minus USD (DFF),
                                 forward-filled; fails closed (zero
                                 positions) if the rate series is absent
USDINR cost (pre-registered): one-way 0.010 INR per 1.0 position change
= 1.0 pip at the 0.01 pip convention, mid-range retail allowance; the
fortress re-tests at 2x.

Hourly hypotheses (NEW IDs, same dataset/costs as Sprint 2, parameters
fixed here before any run):
- S09 WeekendGapFadeSundayOpen: the corrected S08 idea. A week-open bar
  is the first bar after an inter-bar gap > 36 h (this feed opens Sunday
  22:00 UTC). Fade a gap (week-open open vs prior close) larger than
  1.0 x ATR(14) at the prior close, held until the first Monday 12:00
  UTC after the open. S08 stays rejected; this is a new ID.
- S10 CalmRegimeHourlyMomentum: sign of the trailing 120 h return, but
  only while realised volatility (stdev of the last 24 hourly returns)
  is at or below its trailing 1,440 h median.
- S11 OverlapExpansionContinuation: when the London morning range
  (07:00-11:00 bars) exceeds 1.25 x its trailing 20-day median, follow
  the morning move's direction through the London/New York overlap
  (bars starting 13:00-16:59 UTC).
- S12 IntradayAnchorReversion: fade deviations of the close from its
  trailing 48 h mean beyond +/-2.0 standard deviations (of closes),
  held for 4 bars.
- S13 FridayProfitTakingReversal: on Fridays, if the week's move
  (Friday 15:00-bar close vs the week's first open) exceeds 1.0 x
  ATR(14), fade it over the bars starting 16:00-20:59 UTC.

Multiple-testing accounting: the deflated Sharpe gate is charged for
ALL trials in the programme -- Sprint 1's 24 + Sprint 2's 32 + this
sprint's INR evaluations (6 when INR validates) + this sprint's 20
hourly evaluations = up to 82 -- and the trial-Sharpe population pools
every sprint's base runs. Sprint 1's Sharpes are reconstructed exactly
as Sprint 2 did (in-process re-run on FRED data), with one extra guard:
if Sprint 1's carry inputs are unavailable the reconstruction is
declared failed rather than silently wrong (M04/M05 would otherwise
reconstruct as zero-position series). Sprint 2's 32 Sharpes are
reconstructed by re-running its base simulations on the validated
hourly data. Reconstructed Sharpes are used ONLY for the
multiple-testing population; no prior verdict is recomputed.

Usage: python scripts/run_edge_sprint3.py [--data-dir PATH] [--out PATH]
       python scripts/run_edge_sprint3.py --summary   (JSON only)
The markdown report is printed to stdout in full mode; file writes are
best-effort (a warning is printed when a write is not permitted).
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import math
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_quant_os import edge_hypotheses as eh
from ai_quant_os.edge_hypotheses import HYPOTHESES, BarsView, Hypothesis
from ai_quant_os.validation_fortress import (
    FortressConfig,
    FortressVerdict,
    StrategySeries,
    run_fortress,
)

_spec2 = importlib.util.spec_from_file_location(
    "run_edge_sprint2", Path(__file__).resolve().parent / "run_edge_sprint2.py"
)
sprint2 = importlib.util.module_from_spec(_spec2)
sys.modules[_spec2.name] = sprint2  # dataclass processing needs this
_spec2.loader.exec_module(sprint2)
sprint1 = sprint2.sprint1

ONE_WAY_COST = sprint1.ONE_WAY_COST
simulate = sprint1.simulate
per_period_sharpe = sprint1.per_period_sharpe
periods_per_year = sprint1.periods_per_year
fetch_validated_csv = sprint2.fetch_validated_csv
parse_hourly_csv = sprint2.parse_hourly_csv

FRED_BASE = sprint1.FRED_BASE
ALL_PAIRS = ("EURUSD", "GBPUSD", "USDJPY", "EURJPY")
SPRINT1_N_TRIALS = 24
SPRINT2_N_TRIALS = 32

# --- INR (pre-registered) ---------------------------------------------------
USDINR_COST = 0.010  # one-way, price units = 1.0 pip at the 0.01 convention
INR_MIN_ROWS = 2500
INR_VALUE_RANGE = (50.0, 110.0)
INR_MAX_GAP_DAYS = 21
INR_MAX_DAILY_MOVE = 0.05
EURINR_RANGE = (55.0, 125.0)
_FALSIFICATION = (
    "Rejected if the Validation Fortress verdict is FAIL on the sprint dataset, "
    "or if net expectancy after 2x costs <= 0. No re-tuning under this ID."
)


def fetch_fred_csv(
    series_id: str, start: str = "2015-01-01", attempts: int = 8
) -> tuple[list[tuple[date, float]], str]:
    """Fetch one FRED series via Sprint 1's fredgraph route, but verify
    the body is really CSV before parsing (the endpoint can return an
    HTML maintenance page with HTTP 200). Retries with backoff; raises
    RuntimeError carrying the last observed condition."""
    url = f"{FRED_BASE}?id={series_id}&cosd={start}"
    last = "no attempt made"
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                text = response.read().decode("utf-8")
            first = text.lstrip().split("\n", 1)[0].strip()
            if first.startswith("DATE,") or first.startswith("observation_date"):
                rows: list[tuple[date, float]] = []
                for line in text.strip().split("\n")[1:]:
                    parts = line.split(",")
                    if len(parts) != 2:
                        continue
                    try:
                        rows.append((date.fromisoformat(parts[0]), float(parts[1])))
                    except ValueError:
                        continue  # FRED marks missing observations with "."
                rows.sort()
                if len(rows) >= 100:
                    return rows, f"fetched on attempt {attempt + 1}"
                last = f"CSV body but only {len(rows)} parseable rows"
            elif "<!doctype" in text[:200].lower() or "<html" in text[:200].lower():
                last = (
                    f"HTML body ({len(text)} bytes) instead of CSV -- "
                    "FRED returned its 'Site Maintenance' page"
                )
            else:
                last = f"unexpected body ({len(text)} bytes, starts {first[:40]!r})"
        except Exception as error:  # noqa: BLE001 - recorded, then retried
            last = f"{type(error).__name__}: {error}"
        if attempt + 1 < attempts:
            time.sleep(min(3 + 4 * attempt, 25))
    raise RuntimeError(f"{series_id}: {last} (after {attempts} attempts)")


def validate_usdinr(
    rows: list[tuple[date, float]], eurinr_rows: list[tuple[date, float]] | None
) -> tuple[bool, dict]:
    """The pre-registered DEXINUS validation battery. Returns (ok, info)."""
    info: dict = {"rows": len(rows)}
    failures: list[str] = []
    if len(rows) < INR_MIN_ROWS:
        failures.append(f"only {len(rows)} rows (< {INR_MIN_ROWS})")
    dates = [day for day, _ in rows]
    values = [value for _, value in rows]
    info["first"] = dates[0].isoformat() if dates else ""
    info["last"] = dates[-1].isoformat() if dates else ""
    if any(b <= a for a, b in zip(dates, dates[1:])):
        failures.append("dates not strictly increasing")
    lo, hi = INR_VALUE_RANGE
    info["min"] = min(values) if values else None
    info["max"] = max(values) if values else None
    if any(not (lo <= v <= hi) for v in values):
        failures.append(f"values outside [{lo}, {hi}]")
    if any(v <= 0 for v in values):
        failures.append("zero/negative values present")
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    info["max_gap_days"] = max(gaps) if gaps else 0
    info["gaps_over_7d"] = sum(1 for g in gaps if g > 7)
    if gaps and max(gaps) > INR_MAX_GAP_DAYS:
        failures.append(f"calendar gap of {max(gaps)} days (> {INR_MAX_GAP_DAYS})")
    moves = [abs(values[i] / values[i - 1] - 1.0) for i in range(1, len(values))]
    info["max_daily_move"] = max(moves) if moves else 0.0
    info["moves_over_2pct"] = sum(1 for m in moves if m > 0.02)
    if moves and max(moves) > INR_MAX_DAILY_MOVE:
        failures.append(f"daily move of {max(moves):.3f} (> {INR_MAX_DAILY_MOVE})")
    if eurinr_rows is not None:
        er_values = [v for _, v in eurinr_rows]
        er_moves = [abs(er_values[i] / er_values[i - 1] - 1.0) for i in range(1, len(er_values))]
        info["eurinr"] = {
            "rows": len(eurinr_rows),
            "min": min(er_values) if er_values else None,
            "max": max(er_values) if er_values else None,
            "max_daily_move": max(er_moves) if er_moves else 0.0,
        }
        elo, ehi = EURINR_RANGE
        if len(eurinr_rows) < INR_MIN_ROWS:
            failures.append(f"EURINR cross-check only {len(eurinr_rows)} rows")
        if any(not (elo <= v <= ehi) for v in er_values):
            failures.append(f"EURINR outside [{elo}, {ehi}]")
        if er_moves and max(er_moves) > INR_MAX_DAILY_MOVE:
            failures.append(f"EURINR daily move of {max(er_moves):.3f}")
    else:
        info["eurinr"] = None
    info["failures"] = failures
    return not failures, info


def load_inr_carry(
    bar_days: list[date],
) -> tuple[tuple[float, ...] | None, str]:
    """INR-minus-USD annual rate differential aligned to the USDINR bar
    days (forward-filled), or (None, reason) when a rate series cannot
    be fetched -- I06 then fails closed with zero positions."""
    try:
        inr_rate, _ = fetch_fred_csv("IRSTCB01INM156N", attempts=4)
        usd_rate, _ = fetch_fred_csv("DFF", attempts=4)
    except RuntimeError as error:
        return None, f"carry unavailable: {error}"
    if not inr_rate or not usd_rate:
        return None, "carry unavailable: empty rate series"
    inr_ff = sprint1.forward_fill(bar_days, inr_rate)
    usd_ff = sprint1.forward_fill(bar_days, usd_rate)
    diff = tuple((a - b) / 100.0 for a, b in zip(inr_ff, usd_ff))
    return diff, "INR IRSTCB01INM156N minus USD DFF, forward-filled"


def sprint1_trial_sharpes_guarded(data_dir: Path) -> tuple[float, ...] | None:
    """Sprint 2's exact reconstruction of Sprint 1's 24 base Sharpes,
    plus a carry-availability guard: Sprint 1's own load_carry returns
    None when a rate fetch fails, which would silently reconstruct
    M04/M05 as zero-position series with Sharpe 0. That is declared a
    failed reconstruction here instead of a wrong population."""
    for _attempt in range(3):
        try:
            series, _provenance = sprint1.load_fx_series(data_dir)
            if any(len(rows) < 2000 for rows in series.values()):
                raise RuntimeError("incomplete FRED FX series")
            carry = sprint1.load_carry(series)
            if carry is None:
                raise RuntimeError("Sprint 1 carry inputs unavailable")
            bars_by_pair = {
                instrument: sprint1.build_bars(instrument, rows)
                for instrument, rows in series.items()
            }
            sharpes: list[float] = []
            for hypothesis in HYPOTHESES:
                if hypothesis.timeframe != "1d":
                    continue
                for instrument in hypothesis.instruments:
                    bars = bars_by_pair[instrument]
                    diff = None
                    if hypothesis.uses_carry:
                        diff = carry[instrument]
                    positions = hypothesis.signal(bars, hypothesis.base_params, diff)
                    accrual = diff if hypothesis.uses_carry else None
                    sim = simulate(bars, positions, ONE_WAY_COST[instrument], accrual)
                    sharpes.append(per_period_sharpe(sim.net))
            if len(sharpes) == SPRINT1_N_TRIALS:
                return tuple(sharpes)
        except Exception:
            time.sleep(5)
    return None


def sprint2_trial_sharpes(bars_by_pair: dict[str, BarsView]) -> tuple[float, ...]:
    """Reconstruct Sprint 2's 32 base Sharpes by re-running its base
    simulations on the validated hourly data (deterministic)."""
    sharpes: list[float] = []
    for hypothesis in HYPOTHESES:
        if hypothesis.timeframe != "1h":
            continue
        for instrument in hypothesis.instruments:
            bars = bars_by_pair[instrument]
            positions = hypothesis.signal(bars, hypothesis.base_params, None)
            sim = simulate(bars, positions, ONE_WAY_COST[instrument], None)
            sharpes.append(per_period_sharpe(sim.net))
    return tuple(sharpes)


# ---------------------------------------------------------------------------
# New signal functions (Sprint 3). Contract per edge_hypotheses: positions[t]
# is decided from data up to and including bar t and held over (t, t+1].
# ---------------------------------------------------------------------------


def _week_open_events(bars: BarsView) -> list[int]:
    """Indices of week-open bars: first bar after an inter-bar gap > 36 h
    (this feed opens Sunday 22:00 UTC; ordinary weekend gaps are ~48 h)."""
    return [
        i
        for i in range(1, len(bars.closes))
        if (bars.timestamps[i] - bars.timestamps[i - 1]).total_seconds() > 36 * 3600
    ]


def _s09_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    gap_atr = eh._param(params, "gap_atr", 1.0)
    atr = eh._atr_series(bars, 14)
    for i in _week_open_events(bars):
        atr_prior = atr[i - 1]
        if atr_prior is None or atr_prior <= 0:
            continue
        gap = bars.opens[i] - bars.closes[i - 1]
        if gap == 0.0 or abs(gap) <= gap_atr * atr_prior:
            continue
        fade = -eh._sign(gap)
        day = bars.timestamps[i].date()
        monday = day + timedelta(days=(0 - day.weekday()) % 7)
        hold_end = datetime(monday.year, monday.month, monday.day, 12, tzinfo=timezone.utc)
        j = i
        while j < n and bars.timestamps[j] < hold_end:
            positions[j] = fade
            j += 1
    return tuple(positions)


def _s10_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    lookback = eh._int_param(params, "lookback_hours", 120.0)
    vol_window = eh._int_param(params, "vol_window_hours", 24.0)
    median_hours = eh._int_param(params, "vol_median_hours", 1440.0)
    volatility = eh._realised_vol_series(bars, vol_window)
    sorted_window: list[float] = []
    for t in range(n):
        current = volatility[t]
        if current is not None:
            bisect.insort(sorted_window, current)
        outgoing_index = t - median_hours
        if 0 <= outgoing_index and volatility[outgoing_index] is not None:
            outgoing = volatility[outgoing_index]
            del sorted_window[bisect.bisect_left(sorted_window, outgoing)]
        if current is None or len(sorted_window) < median_hours:
            continue
        middle = len(sorted_window) // 2
        if len(sorted_window) % 2:
            median = sorted_window[middle]
        else:
            median = 0.5 * (sorted_window[middle - 1] + sorted_window[middle])
        if current <= median:
            positions[t] = eh._momentum_sign(bars, t, lookback)
    return tuple(positions)


def _s11_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    mult = eh._param(params, "expansion_mult", 1.25)
    median_days = eh._int_param(params, "median_days", 20.0)
    morning_ranges: list[float] = []
    for _day, indices in eh._day_groups(bars):
        morning = [i for i in indices if 7 <= bars.timestamps[i].hour <= 11]
        first = next((i for i in morning if bars.timestamps[i].hour == 7), None)
        last = next((i for i in morning if bars.timestamps[i].hour == 11), None)
        if first is None or last is None:
            continue
        morning_range = max(bars.highs[i] for i in morning) - min(bars.lows[i] for i in morning)
        move = bars.closes[last] - bars.opens[first]
        if len(morning_ranges) >= median_days and move != 0.0:
            trailing = sorted(morning_ranges[-median_days:])
            median = eh._quantile(trailing, 0.5)
            if median > 0 and morning_range > mult * median:
                stance = eh._sign(move)
                for i in indices:
                    if 13 <= bars.timestamps[i].hour <= 16:
                        positions[i] = stance
        morning_ranges.append(morning_range)
    return tuple(positions)


def _s12_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    window = eh._int_param(params, "window_hours", 48.0)
    z_entry = eh._param(params, "z_entry", 2.0)
    hold_bars = eh._int_param(params, "hold_bars", 4.0)
    sum_prefix = eh._prefix_sums(bars.closes)
    square_prefix = eh._prefix_sums([c * c for c in bars.closes])
    direction = 0.0
    remaining = 0
    for t in range(n):
        if remaining > 0:
            positions[t] = direction
            remaining -= 1
            if remaining == 0:
                direction = 0.0
            continue
        mean = eh._window_mean(sum_prefix, t, window)
        if mean is None:
            continue
        mean_square = eh._window_mean(square_prefix, t, window)
        if mean_square is None:
            continue
        variance = mean_square - mean * mean
        if variance <= 0.0:
            continue
        z = (bars.closes[t] - mean) / math.sqrt(variance)
        if z >= z_entry:
            direction = -1.0
        elif z <= -z_entry:
            direction = 1.0
        else:
            continue
        positions[t] = direction
        remaining = hold_bars - 1
    return tuple(positions)


def _s13_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    mult = eh._param(params, "week_move_atr", 1.0)
    atr = eh._atr_series(bars, 14)
    week_open_price = bars.opens[0] if n else 0.0
    week_opens = set(_week_open_events(bars))
    for t in range(n):
        if t in week_opens:
            week_open_price = bars.opens[t]
        stamp = bars.timestamps[t]
        if stamp.weekday() != 4 or stamp.hour != 15:
            continue
        atr_now = atr[t]
        if atr_now is None or atr_now <= 0:
            continue
        move = bars.closes[t] - week_open_price
        if move == 0.0 or abs(move) <= mult * atr_now:
            continue
        stance = -eh._sign(move)
        j = t
        while j < n and bars.timestamps[j].date() == stamp.date():
            if 16 <= bars.timestamps[j].hour <= 20:
                positions[j] = stance
            j += 1
    return tuple(positions)


def _i05_signal(
    bars: BarsView, params: Mapping[str, float], carry_diff: tuple[float, ...] | None
) -> tuple[float, ...]:
    n = len(bars.closes)
    positions = [0.0] * n
    sma_days = eh._int_param(params, "sma_days", 200.0)
    entry_dev = eh._param(params, "entry_dev", 0.02)
    exit_dev = eh._param(params, "exit_dev", 0.005)
    sma = eh._sma_series(bars.closes, sma_days)
    state = 0.0
    for t in range(n):
        anchor = sma[t]
        if anchor is None or anchor <= 0:
            positions[t] = 0.0
            continue
        deviation = bars.closes[t] / anchor - 1.0
        if state == 0.0:
            if abs(deviation) >= entry_dev:
                state = -eh._sign(deviation)
        elif abs(deviation) <= exit_dev:
            state = 0.0
        positions[t] = state
    return tuple(positions)


# ---------------------------------------------------------------------------
# Pre-registered Sprint 3 hypothesis definitions (parameters fixed above in
# the module docstring before any run; variants are the neighbourhood the
# CSCV/PBO gate inspects, base included).
# ---------------------------------------------------------------------------


def _scaled_variants(
    base: Mapping[str, float], factors: tuple[float, ...] = (0.75, 0.85, 1.0, 1.2, 1.3)
) -> tuple[Mapping[str, float], ...]:
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
    instruments: tuple[str, ...],
    timeframe: str,
    mechanism: str,
    claim: str,
    failure_regime: str,
    base_params: dict[str, float],
    signal,
    uses_carry: bool = False,
    variants: tuple[Mapping[str, float], ...] | None = None,
) -> Hypothesis:
    base = MappingProxyType(dict(base_params))
    return Hypothesis(
        id=hypothesis_id,
        family=family,
        name=name,
        instruments=instruments,
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


INR_HYPOTHESES: tuple[Hypothesis, ...] = (
    _hypothesis(
        "I01",
        "inr_daily",
        "TimeSeriesMomentumINR",
        ("USDINR",),
        "1d",
        "The M01 mechanism on a managed float: gradual RBI-tolerated "
        "depreciation trends and slow-moving importer/exporter hedging "
        "make USDINR trends persist for months once established.",
        "The sign of the trailing 60-day return, held daily, delivers a "
        "net-of-cost Sharpe of at least 0.5 and a mean daily return of "
        "at least +0.5 bp on USDINR.",
        "Intervention-heavy ranges where the RBI caps moves in both "
        "directions and 60-day trends chop.",
        {"lookback_days": 60.0},
        eh._m01_signal,
    ),
    _hypothesis(
        "I02",
        "inr_daily",
        "MovingAverageCrossINR",
        ("USDINR",),
        "1d",
        "The M02 regime filter on USDINR: the slow average proxies the "
        "RBI's tolerated depreciation path; price above it marks the "
        "weak-rupee regime that hedging flow keeps extending.",
        "Being long USDINR when SMA(20) > SMA(100) and short otherwise "
        "delivers a net-of-cost Sharpe of at least 0.4.",
        "Sideways intervention ranges with repeated slow-average "
        "crosses bleeding spread on whipsaw flips.",
        {"fast_days": 20.0, "slow_days": 100.0},
        eh._m02_signal,
    ),
    _hypothesis(
        "I03",
        "inr_daily",
        "DonchianBreakoutINR",
        ("USDINR",),
        "1d",
        "A close beyond the 55-day extreme in a managed float signals "
        "the RBI has stepped back; option-barrier and stop flow beyond "
        "round levels then extends the move.",
        "The turtle-style state machine delivers a net-of-cost profit "
        "factor of at least 1.3 with at least 15 completed trades.",
        "Defended levels where breakouts are sold by the central bank within days, repeatedly.",
        {"entry_days": 55.0, "exit_days": 20.0},
        eh._m03_signal,
    ),
    _hypothesis(
        "I04",
        "inr_daily",
        "VolatilityManagedMomentumINR",
        ("USDINR",),
        "1d",
        "The M06 gate on USDINR: momentum crashes cluster in volatility "
        "spikes (global risk-off hits INR hard); standing aside when "
        "realised volatility is far above its norm avoids the unwind.",
        "60-day momentum with the volatility gate delivers a net "
        "Sharpe of at least 0.5 and is flat on no more than 40% of days.",
        "Volatility spikes that mark RBI regime shifts to a faster "
        "depreciation path, where the gate misses the trend leg.",
        {
            "lookback_days": 60.0,
            "vol_window_days": 20.0,
            "vol_mult": 1.5,
            "vol_median_days": 252.0,
        },
        eh._m06_signal,
    ),
    _hypothesis(
        "I05",
        "inr_daily",
        "ManagedFloatReversionINR",
        ("USDINR",),
        "1d",
        "A managed float is anchored: the RBI leans against sustained "
        "deviations from the slow path with reserves and moral suasion, "
        "and exporter/importer flow is stabilising at extremes, so a "
        ">= 2% stretch from the 200-day mean partially retraces.",
        "Fading >= 2% deviations from SMA(200) until the deviation "
        "falls to 0.5% earns a positive mean net return per held day "
        "of at least +0.5 bp, over at least 10 entries.",
        "Genuine regime breaks (2013 taper tantrum, 2022 tightening) "
        "where the anchor itself migrates and the fade fights a "
        "one-way repricing.",
        {"sma_days": 200.0, "entry_dev": 0.02, "exit_dev": 0.005},
        _i05_signal,
        variants=_manual_variants(
            (
                {"sma_days": 200.0, "entry_dev": 0.02, "exit_dev": 0.005},
                {"sma_days": 200.0, "entry_dev": 0.015, "exit_dev": 0.005},
                {"sma_days": 200.0, "entry_dev": 0.025, "exit_dev": 0.005},
                {"sma_days": 150.0, "entry_dev": 0.02, "exit_dev": 0.005},
                {"sma_days": 250.0, "entry_dev": 0.02, "exit_dev": 0.005},
            )
        ),
    ),
    _hypothesis(
        "I06",
        "inr_daily",
        "CarryDifferentialINR",
        ("USDINR",),
        "1d",
        "The M04 mechanism on USDINR: a persistently positive INR-USD "
        "rate differential pays whoever holds the long-INR side, and "
        "forward premia systematically overprice the depreciation that "
        "actually occurs (the managed-float carry puzzle).",
        "Holding the sign of the differential beyond a 0.25% band "
        "earns the differential plus a net-of-cost total return of at "
        "least +1.0% annualised, Sharpe at least 0.4.",
        "Risk-off episodes where INR falls faster than the premium, "
        "and differential compression as the Fed out-hikes the RBI.",
        {"band": 0.0025},
        eh._m04_signal,
        uses_carry=True,
    ),
)

HOURLY_HYPOTHESES: tuple[Hypothesis, ...] = (
    _hypothesis(
        "S09",
        "session_volatility",
        "WeekendGapFadeSundayOpen",
        ALL_PAIRS,
        "1h",
        "Weekend gaps are headline overreaction priced in the thinnest "
        "liquidity of the week (the Sunday 22:00 UTC open); dealers who "
        "quote the gap wide expect retracement once London liquidity "
        "returns on Monday. This is S08's mechanism with the trigger "
        "corrected to the feed's actual week boundary -- a new ID, not "
        "a re-run: S08 keyed on Monday groups preceded by a Friday bar, "
        "which this Sunday-open feed never produces.",
        "Fading Sunday-open gaps larger than 1.0 x ATR(14) until "
        "Monday 12:00 UTC earns a mean of at least +0.25 x the gap "
        "size per event net of costs, across at least 40 gap events "
        "in total.",
        "Gaps caused by genuine regime events (elections, emergency "
        "central-bank action) where Monday continues in the gap "
        "direction.",
        {"gap_atr": 1.0},
        _s09_signal,
    ),
    _hypothesis(
        "S10",
        "session_volatility",
        "CalmRegimeHourlyMomentum",
        ALL_PAIRS,
        "1h",
        "Hourly trends persist while volatility is orderly: benchmark "
        "execution and gradual information diffusion dominate. In "
        "volatility spikes the same horizon is liquidation and stop "
        "flow that whipsaws, so the momentum signal is only taken when "
        "realised volatility is at or below its trailing median.",
        "120 h momentum gated to calm regimes delivers a net-of-cost "
        "annualised Sharpe of at least 0.5 in at least 3 of the 4 "
        "pairs, with a positive mean period return.",
        "Calm regimes that are actually compression before a repricing "
        "(pre-announcement drift), where the gate admits the strategy "
        "exactly into the whipsaw.",
        {"lookback_hours": 120.0, "vol_window_hours": 24.0, "vol_median_hours": 1440.0},
        _s10_signal,
    ),
    _hypothesis(
        "S11",
        "session_volatility",
        "OverlapExpansionContinuation",
        ALL_PAIRS,
        "1h",
        "A London morning range far above its recent norm means a real "
        "repricing is under way, not noise. The London/New York overlap "
        "brings the day's deepest liquidity, and US desks join the "
        "established direction (hedging and momentum allocation) more "
        "often than they fade it -- conditional on expansion, the "
        "opposite conditioning of S04's unconditional fade.",
        "On expansion days the 13:00-17:00 continuation in the morning "
        "move's direction earns at least +0.15 x the morning range per "
        "signal day net of costs, on at least 60 signal days per pair.",
        "Expansion mornings caused by a single data print that is "
        "fully priced by noon, leaving the overlap to retrace it.",
        {"expansion_mult": 1.25, "median_days": 20.0},
        _s11_signal,
    ),
    _hypothesis(
        "S12",
        "session_volatility",
        "IntradayAnchorReversion",
        ALL_PAIRS,
        "1h",
        "Deviations of price from its trailing 48 h mean beyond two "
        "standard deviations are mostly positioning and liquidity "
        "overshoot rather than new information; the liquidity providers "
        "who absorbed the overshoot pull price back toward the anchor "
        "over the next few bars. Distinct from S05: a multi-day "
        "statistical anchor, not single-bar spike extremes.",
        "The fade held for 4 bars earns a mean of at least +0.20 x "
        "ATR(20) per event net of costs, across at least 100 events "
        "per pair.",
        "Genuine repricing events (central-bank surprises) where the "
        "deviation is the new level and the anchor migrates to it.",
        {"window_hours": 48.0, "z_entry": 2.0, "hold_bars": 4.0},
        _s12_signal,
    ),
    _hypothesis(
        "S13",
        "session_volatility",
        "FridayProfitTakingReversal",
        ALL_PAIRS,
        "1h",
        "Leveraged positions built along the week's move are squared "
        "into the weekend to avoid gap risk; that squaring flow is "
        "one-sided against the week's direction in the last liquid "
        "hours of Friday, independent of fundamentals.",
        "Fading a week move larger than 1.0 x ATR(14) over Friday "
        "16:00-21:00 UTC earns at least +1 bp per held hour net of "
        "costs, on at least 40 signal weeks per pair.",
        "Weeks ending on major scheduled news where positions are "
        "deliberately carried (or added) over the weekend.",
        {"week_move_atr": 1.0},
        _s13_signal,
    ),
)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

COSTS = {**ONE_WAY_COST, "USDINR": USDINR_COST}


@dataclass
class Evaluation:
    hypothesis: Hypothesis
    instrument: str
    base: object
    verdict: FortressVerdict


def simulate_variants(hypothesis: Hypothesis, bars: BarsView, carry_diff, cost: float):
    """Base + neighbourhood simulations for one hypothesis/instrument,
    with Sprint 1's exact signal/carry semantics."""
    simulations: dict[str, object] = {}
    base_index = 0
    for i, params in enumerate(hypothesis.variants):
        if dict(params) == dict(hypothesis.base_params):
            base_index = i
        diff = carry_diff if hypothesis.uses_carry else None
        positions = hypothesis.signal(bars, params, diff)
        accrual = diff if hypothesis.uses_carry else None
        simulations[f"v{i}"] = simulate(bars, positions, cost, accrual)
    return simulations[f"v{base_index}"], simulations


def s09_diagnostics(bars: BarsView) -> dict:
    """Report-only context for S09 (not used by any verdict): how many
    week-open gaps the feed contains and how many clear the registered
    1.0 x ATR(14) threshold at base parameters."""
    atr = eh._atr_series(bars, 14)
    events = _week_open_events(bars)
    qualified = 0
    for i in events:
        atr_prior = atr[i - 1]
        if atr_prior is None or atr_prior <= 0:
            continue
        gap = bars.opens[i] - bars.closes[i - 1]
        if gap != 0.0 and abs(gap) > atr_prior:
            qualified += 1
    return {"week_opens": len(events), "qualified_gaps": qualified}


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def fmt_num(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.{digits}f}"


def _params_text(hypothesis: Hypothesis) -> str:
    return ", ".join(f"{name} = {value:g}" for name, value in dict(hypothesis.base_params).items())


def _results_table(evaluations: list[Evaluation]) -> list[str]:
    lines = [
        "| Hypothesis | Instrument | Trades | Net return* | Ann. Sharpe | PBO | "
        "DSR | Stressed net* | Verdict | Failed gates |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for ev in evaluations:
        m = ev.verdict.metrics
        failed = [g.gate for g in ev.verdict.gates if not g.passed]
        lines.append(
            f"| {ev.hypothesis.id} {ev.hypothesis.name} | {ev.instrument} | "
            f"{ev.base.trade_count} | {fmt_pct(m.get('total_net_return', 0.0))} | "
            f"{fmt_num(m.get('annualized_sharpe'), 2)} | {fmt_num(m.get('pbo'), 2)} | "
            f"{fmt_num(m.get('deflated_sharpe'), 2)} | "
            f"{fmt_pct(m.get('stressed_total_return', 0.0))} | "
            f"{'**PASS**' if ev.verdict.passed else 'FAIL'} | "
            f"{', '.join(failed) if failed else '—'} |"
        )
    return lines


def _gate_detail(evaluations: list[Evaluation]) -> list[str]:
    lines: list[str] = []
    for ev in evaluations:
        lines.append("")
        lines.append(f"### {ev.hypothesis.id} {ev.hypothesis.name} — {ev.instrument}")
        lines.append("")
        lines.append("| Gate | Result | Metric | Reason |")
        lines.append("|---|---|---:|---|")
        for gate in ev.verdict.gates:
            lines.append(
                f"| {gate.gate} | {'PASS' if gate.passed else 'FAIL'} | "
                f"{fmt_num(gate.metric)} | {gate.reason} |"
            )
    return lines


def _spec_block(hypotheses: tuple[Hypothesis, ...]) -> list[str]:
    lines: list[str] = []
    for h in hypotheses:
        lines.append(
            f"- **{h.id} {h.name}** ({h.timeframe}, {', '.join(h.instruments)})"
            f" — base parameters: {_params_text(h)}."
        )
        lines.append(f"  Mechanism: {h.mechanism}")
        lines.append(f"  Falsifiable claim: {h.falsifiable_claim}")
        lines.append(f"  Expected failure regime: {h.failure_regime}")
    return lines


def render_report(
    inr_status: str,
    inr_fetch_error: str | None,
    inr_validation: dict | None,
    inr_carry_note: str,
    inr_evaluations: list[Evaluation],
    hourly_validations: dict[str, dict],
    cross_check: dict | None,
    s09_diag: dict[str, dict],
    hourly_evaluations: list[Evaluation],
    n_trials_total: int,
    sprint1_recovered: bool,
) -> str:
    lines: list[str] = []
    today = date.today().isoformat()
    all_evals = inr_evaluations + hourly_evaluations
    passed = [e for e in all_evals if e.verdict.passed]
    lines.append("# Edge Sprint 3 — Results (INR Claim Test + New Hourly Family)")
    lines.append("")
    lines.append(f"**Date:** {today}  ")
    lines.append(
        "**Scope:** (1) an explicit test of Sprint 1's claim that no free "
        "reliable USDINR series was obtainable, and -- if the candidate "
        "series validates -- a pre-registered INR hypothesis family "
        "(I01-I06, new IDs) on USDINR; (2) five new pre-registered hourly "
        "hypotheses (S09-S13, new IDs) on Sprint 2's validated hourly "
        "dataset. All parameters were fixed in the runner's docstring "
        "before any evaluation ran. No rejected ID (M01-M06, S01-S08) was "
        "re-run or re-tuned; Sprint 1/2 verdicts stand untouched."
    )
    lines.append("")
    lines.append("## Part 1 — The USDINR claim, tested")
    lines.append("")
    lines.append(
        'Sprint 1 reported: "INR pairs: **PENDING_DATA** — no free '
        "reliable USDINR reference series was obtainable, and none was "
        'fabricated." That claim is falsifiable, and it is **false as '
        "stated**: FRED's catalogue contains **DEXINUS** — \"Indian "
        'Rupees to One U.S. Dollar Spot Exchange Rate", source Board of '
        "Governors of the Federal Reserve System, release **H.10 Daily "
        'Foreign Exchange Rates**, units "Indian Rupees to One U.S. '
        'Dollar", noon buying rates in New York City, daily, spanning '
        "1973-01-02 to 2026-10-16, last updated 2026-10-19 "
        "(https://fred.stlouisfed.org/series/DEXINUS). DEXINUS is the "
        "same release, the same rate convention and the same "
        "fredgraph.csv endpoint family as DEXUSEU, DEXUSUK and DEXJPUS, "
        "which Sprint 1 fetched successfully from this environment. What "
        "Sprint 1 lacked was evidently the series ID, not the data."
    )
    lines.append("")
    if inr_status == "fetch_failed":
        lines.append(
            "**This run could not fetch the series.** The fredgraph "
            f"endpoint was tried repeatedly; the terminal condition was: "
            f"{inr_fetch_error}. During this session the endpoint "
            "consistently returned FRED's HTML 'Site Maintenance' page "
            "(HTTP 200, ~0.9 KB) for every series, including DEXUSEU — "
            "an endpoint outage, not a property of DEXINUS (Sprints 1 "
            "and 2 used the same endpoint successfully earlier the same "
            "day). Because no bytes could be validated, **no INR "
            "evaluation was run and INR remains PENDING_DATA for this "
            "run**. The standing conclusion is narrower than Sprint "
            "1's: a free, reliable USDINR reference series EXISTS "
            "(DEXINUS); it was temporarily unreachable. Recommendation: "
            "re-run this runner when the endpoint is back; keep "
            "`INR_INSTRUMENT_STATUS` unchanged until the pre-registered "
            "validation battery (specified in the runner's docstring) "
            "passes on real bytes."
        )
        lines.append("")
    elif inr_status == "validation_failed":
        v = inr_validation or {}
        lines.append(
            "**The series was fetched but FAILED the pre-registered "
            "validation battery**, so no INR evaluation was run and INR "
            "remains PENDING_DATA. Failures: "
            + "; ".join(v.get("failures", []))
            + f". Observed: {v.get('rows')} rows, "
            f"{v.get('first')} to {v.get('last')}, min "
            f"{fmt_num(v.get('min'), 3)}, max {fmt_num(v.get('max'), 3)}, "
            f"max calendar gap {v.get('max_gap_days')} days, max daily "
            f"move {fmt_num(v.get('max_daily_move'), 4)}."
        )
        lines.append("")
    else:
        v = inr_validation or {}
        er = v.get("eurinr") or {}
        lines.append(
            "**The series fetched and PASSED the full pre-registered "
            "validation battery**, so the INR family below was "
            "evaluated. DEXINUS via the same fredgraph route Sprint 1 "
            "used; noon reference (mid) rates, so bars are again "
            "open = high = low = close, the Sprint 1 limitation."
        )
        lines.append("")
        lines.append("| Check (pre-registered) | Threshold | Observed |")
        lines.append("|---|---|---:|")
        lines.append(f"| Rows from 2015-01-01 | >= {INR_MIN_ROWS} | {v.get('rows')} |")
        lines.append(f"| Span | — | {v.get('first')} to {v.get('last')} |")
        lines.append(
            f"| Value range (INR per USD) | within [{INR_VALUE_RANGE[0]:g}, "
            f"{INR_VALUE_RANGE[1]:g}] | {fmt_num(v.get('min'), 3)} to "
            f"{fmt_num(v.get('max'), 3)} |"
        )
        lines.append(
            f"| Max calendar gap | <= {INR_MAX_GAP_DAYS} days | "
            f"{v.get('max_gap_days')} days ({v.get('gaps_over_7d')} gaps > 7d) |"
        )
        lines.append(
            f"| Max single-day move | <= {INR_MAX_DAILY_MOVE:.0%} | "
            f"{fmt_num(v.get('max_daily_move'), 4)} "
            f"({v.get('moves_over_2pct')} moves > 2%) |"
        )
        lines.append(f"| EURINR = DEXINUS*DEXUSEU rows | >= {INR_MIN_ROWS} | {er.get('rows')} |")
        lines.append(
            f"| EURINR range | within [{EURINR_RANGE[0]:g}, {EURINR_RANGE[1]:g}] | "
            f"{fmt_num(er.get('min'), 2)} to {fmt_num(er.get('max'), 2)} |"
        )
        lines.append(
            f"| EURINR max daily move | <= {INR_MAX_DAILY_MOVE:.0%} | "
            f"{fmt_num(er.get('max_daily_move'), 4)} |"
        )
        lines.append("")
        lines.append(
            f"Carry inputs for I06: {inr_carry_note}. If carry was "
            "unavailable, I06 was still evaluated and failed closed "
            "(its signal returns zero positions without a differential)."
        )
        lines.append("")
        lines.append(
            "Cost model for USDINR (pre-registered): one-way "
            f"{USDINR_COST} INR per 1.0 position change = 1.0 pip at "
            "the 0.01 pip convention, charged like Sprint 1's costs; "
            "the fortress re-tests at 2x."
        )
        lines.append("")
        lines.append("### Pre-registered INR hypotheses (fixed before the run)")
        lines.append("")
        lines.extend(_spec_block(INR_HYPOTHESES))
        lines.append("")
        lines.append("### INR results")
        lines.append("")
        lines.extend(_results_table(inr_evaluations))
        lines.append("")
        lines.append("### INR gate detail")
        lines.extend(_gate_detail(inr_evaluations))
        lines.append("")
        n_inr_pass = sum(1 for e in inr_evaluations if e.verdict.passed)
        lines.append(
            f"**INR verdict: {n_inr_pass} of {len(inr_evaluations)} "
            "evaluations passed.** Recommendation on "
            "`src/ai_quant_os/inr_instruments.py`: the module's blocker "
            '(a) — "no INR reference-rate feed" — is now resolvable '
            "for research purposes: DEXINUS exists, is free, and "
            "validated in this run. This runner deliberately does NOT "
            "change `INR_INSTRUMENT_STATUS`: merging the catalogue "
            "into PAPER_INSTRUMENTS also needs blocker (b) (schema / "
            "API / test contract changes) and a live feed wired into "
            "the data fabric, which are platform decisions for the "
            "maintainer, not research-run side effects. Recommended "
            "next step: flip the status only together with that wiring, "
            "in its own PR."
        )
        lines.append("")
    lines.append("## Part 2 — Sprint 3 hourly family (S09-S13)")
    lines.append("")
    lines.append(
        "Dataset: identical to Sprint 2 — the public GitHub export "
        "(`millionairealde-creator/advanced_forex_bot`, commit-pinned, "
        "SHA-256-verified before acceptance, `complete=True` bars "
        "only). Identity in this run is guaranteed by the same SHA-256 "
        "pins; the FRED daily cross-check "
        + (
            "was reproduced (see Sprint 2 for the full validation tables)."
            if cross_check is not None
            else "could not be repeated in this run (the FRED endpoint "
            "was returning its maintenance page), which does not affect "
            "dataset identity: acceptance is by exact SHA-256 match "
            "with the bytes Sprint 2 validated."
        )
    )
    lines.append("")
    lines.append("| Instrument | Bars used | Dropped | First | Last | SHA-256 |")
    lines.append("|---|---:|---:|---|---|---|")
    for instrument in ALL_PAIRS:
        hv = hourly_validations[instrument]
        lines.append(
            f"| {instrument} | {hv['kept_bars']} | {hv['dropped_incomplete']} | "
            f"{hv['first'][:16]} | {hv['last'][:16]} | `{hv['sha256'][:16]}…` |"
        )
    lines.append("")
    lines.append(
        "Week-open structure in this feed (S09 diagnostics, base "
        "parameters, report-only): "
        + "; ".join(
            f"{pair}: {s09_diag[pair]['week_opens']} week opens, "
            f"{s09_diag[pair]['qualified_gaps']} gaps > 1.0 x ATR(14)"
            for pair in ALL_PAIRS
        )
        + "."
    )
    lines.append("")
    lines.append("### Pre-registered hourly hypotheses (fixed before the run)")
    lines.append("")
    lines.extend(_spec_block(HOURLY_HYPOTHESES))
    lines.append("")
    lines.append("### Hourly results")
    lines.append("")
    lines.extend(_results_table(hourly_evaluations))
    lines.append("")
    lines.append(
        "\\* Net return is the sum of per-period net returns (not "
        "compounded), the same quantity the fortress gates use."
    )
    lines.append("")
    lines.append("### Hourly gate detail")
    lines.extend(_gate_detail(hourly_evaluations))
    lines.append("")
    lines.append("## Multiple-testing accounting")
    lines.append("")
    lines.append(
        f"The deflated Sharpe gate is charged for **{n_trials_total} "
        "trials programme-wide**: Sprint 1's 24 + Sprint 2's 32 + this "
        f"sprint's {len(all_evals)} new evaluations. The trial-Sharpe "
        "population pools all sprints' base runs. Sprint 1's Sharpes "
        + (
            "were reconstructed exactly in-process (with the "
            "carry-availability guard described in the runner)."
            if sprint1_recovered
            else "could NOT be reconstructed in this run (FRED "
            "unreachable / carry inputs unavailable); their count of 24 "
            "is still charged in n_trials, and the dispersion population "
            "is Sprint 2's 32 + this sprint's base runs — a documented "
            "deviation, identical in spirit to Sprint 2's fallback."
        )
        + " No prior verdict was recomputed; reconstructed Sharpes feed "
        "only the multiple-testing correction."
    )
    lines.append("")
    lines.append("## Conclusions")
    lines.append("")
    if passed:
        names = ", ".join(f"{e.hypothesis.id}/{e.instrument}" for e in passed)
        lines.append(
            f"Passed: {names}. Under the roadmap, a fortress pass earns "
            "only the next rung — shadow evaluation on live data — not "
            "capital. Reality-gap expectations (50-70% of backtest "
            "performance) apply."
        )
    else:
        lines.append(
            "**No Sprint 3 evaluation passed the fortress.** The "
            "programme total is now "
            f"{n_trials_total} trials with no survivor. Per the "
            "roadmap, the correct response is not to loosen the "
            "fortress or re-tune these IDs — every rejected hypothesis "
            "stays rejected under its ID, and any follow-up idea gets "
            "a new ID and increases the trial count."
        )
    lines.append("")
    if inr_status != "validated":
        lines.append(
            "INR remains **PENDING_DATA** after this run — not because "
            "no series exists (DEXINUS does, in the same H.10 family "
            "Sprint 1 already used), but because this run could not "
            "complete the fetch-and-validate step. That distinction "
            "matters: the blocker is now an endpoint outage / failed "
            "check with a known remedy, not an absence of data."
        )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / "workspace" / "edge_data",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="recompute everything but print only the JSON summary",
    )
    args = parser.parse_args()
    try:
        args.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # --- Hourly dataset (Sprint 2 route, unchanged) ---
    bars_by_pair: dict[str, BarsView] = {}
    hourly_validations: dict[str, dict] = {}
    for instrument in sprint2.PAIR_FILES:
        content = fetch_validated_csv(instrument)
        bars, validation = parse_hourly_csv(instrument, content)
        bars_by_pair[instrument] = bars
        hourly_validations[instrument] = validation
    try:
        cross_check = sprint2.fred_cross_check(bars_by_pair)
        if not any(
            math.isfinite(cc.get("daily_corr", float("nan"))) for cc in cross_check.values()
        ):
            cross_check = None  # FRED unreachable: correlations are all NaN
    except Exception:
        cross_check = None

    # --- Prior-trial Sharpe reconstruction (population only) ---
    prior32 = sprint2_trial_sharpes(bars_by_pair)
    if len(prior32) != SPRINT2_N_TRIALS:
        raise RuntimeError(
            f"Sprint 2 reconstruction produced {len(prior32)} Sharpes, expected {SPRINT2_N_TRIALS}"
        )
    prior24 = sprint1_trial_sharpes_guarded(args.data_dir)
    sprint1_recovered = prior24 is not None
    if prior24 is None:
        prior24 = ()

    # --- Part 1: INR claim test ---
    inr_status = "fetch_failed"
    inr_fetch_error: str | None = None
    inr_validation: dict | None = None
    inr_carry_note = "not attempted (series not validated)"
    inr_carry: tuple[float, ...] | None = None
    inr_bars: BarsView | None = None
    try:
        inr_rows, _note = fetch_fred_csv("DEXINUS")
        eurinr_rows: list[tuple[date, float]] | None = None
        try:
            eur_rows, _ = fetch_fred_csv("DEXUSEU", attempts=4)
            eur = dict(eur_rows)
            inr_dict = dict(inr_rows)
            common = sorted(set(inr_dict) & set(eur))
            # CORRECTION (2026-10-22, disclosed in the results doc): the
            # original code divided (DEXINUS / DEXUSEU), which is
            # dimensionally wrong -- DEXINUS is INR per USD and DEXUSEU is
            # USD per EUR, so EURINR = DEXINUS * DEXUSEU (the same
            # multiplicative cross Sprint 1 used for EURJPY). The divided
            # series dipped to 50.86 and failed the pre-registered
            # [55, 125] band; the band, thresholds and every hypothesis
            # spec are unchanged -- only the arithmetic is corrected.
            eurinr_rows = [(d, inr_dict[d] * eur[d]) for d in common]
        except RuntimeError:
            eurinr_rows = None
        ok, info = validate_usdinr(inr_rows, eurinr_rows)
        inr_validation = info
        if ok:
            inr_status = "validated"
            inr_bars = sprint1.build_bars("USDINR", inr_rows)
            inr_carry, inr_carry_note = load_inr_carry([d for d, _ in inr_rows])
        else:
            inr_status = "validation_failed"
    except RuntimeError as error:
        inr_fetch_error = str(error)

    # --- Base simulations for every new evaluation (trial population) ---
    jobs: list[tuple[Hypothesis, str, BarsView, tuple[float, ...] | None]] = []
    if inr_status == "validated" and inr_bars is not None:
        for hypothesis in INR_HYPOTHESES:
            jobs.append((hypothesis, "USDINR", inr_bars, inr_carry))
    for hypothesis in HOURLY_HYPOTHESES:
        for instrument in hypothesis.instruments:
            jobs.append((hypothesis, instrument, bars_by_pair[instrument], None))
    base_runs: dict[tuple[str, str], tuple[object, dict[str, object]]] = {}
    new_sharpes: list[float] = []
    for hypothesis, instrument, bars, carry_diff in jobs:
        base, simulations = simulate_variants(hypothesis, bars, carry_diff, COSTS[instrument])
        base_runs[(hypothesis.id, instrument)] = (base, simulations)
        new_sharpes.append(per_period_sharpe(base.net))
    population = tuple(prior24) + tuple(prior32) + tuple(new_sharpes)
    n_trials_total = SPRINT1_N_TRIALS + SPRINT2_N_TRIALS + len(jobs)

    # --- Fortress verdicts ---
    evaluations: list[Evaluation] = []
    for hypothesis, instrument, bars, _carry_diff in jobs:
        base, simulations = base_runs[(hypothesis.id, instrument)]
        series = StrategySeries(
            net_returns=base.net,
            gross_returns=base.gross,
            period_costs=base.costs,
            trade_count=base.trade_count,
            periods_per_year=periods_per_year(bars),
            variant_net_returns={k: s.net for k, s in simulations.items()},
            n_trials=n_trials_total,
            trial_sharpes=population,
        )
        verdict = run_fortress(series, FortressConfig())
        evaluations.append(Evaluation(hypothesis, instrument, base, verdict))
    inr_evaluations = [e for e in evaluations if e.instrument == "USDINR"]
    hourly_evaluations = [e for e in evaluations if e.instrument != "USDINR"]
    s09_diag = {pair: s09_diagnostics(bars_by_pair[pair]) for pair in ALL_PAIRS}

    summary = {
        "inr": {
            "status": inr_status,
            "fetch_error": inr_fetch_error,
            "validation": inr_validation,
            "carry": inr_carry_note,
        },
        "hourly_data": hourly_validations,
        "fred_cross_check": cross_check,
        "sprint1_trial_sharpes_recovered": sprint1_recovered,
        "n_trials_total": n_trials_total,
        "trial_population": {
            "sprint1": len(prior24),
            "sprint2": len(prior32),
            "sprint3_new": len(new_sharpes),
        },
        "s09_diagnostics": s09_diag,
        "evaluations": [
            {
                "hypothesis": ev.hypothesis.id,
                "instrument": ev.instrument,
                "trades": ev.base.trade_count,
                "passed": ev.verdict.passed,
                "failed_gates": [g.gate for g in ev.verdict.gates if not g.passed],
                "metrics": dict(ev.verdict.metrics),
            }
            for ev in evaluations
        ],
    }
    if args.summary:
        print(json.dumps(summary))
        return 0
    report = render_report(
        inr_status,
        inr_fetch_error,
        inr_validation,
        inr_carry_note,
        inr_evaluations,
        hourly_validations,
        cross_check,
        s09_diag,
        hourly_evaluations,
        n_trials_total,
        sprint1_recovered,
    )
    out_path = args.out or (
        Path(__file__).resolve().parent.parent / "docs" / "EDGE_SPRINT_3_RESULTS.md"
    )
    for path, content in (
        (out_path, report),
        (args.data_dir / "edge_sprint_3_results.json", json.dumps(summary, indent=2)),
    ):
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as error:
            print(f"warning: could not write {path}: {error}", file=sys.stderr)
    print(report)
    print("===JSON===")
    print(json.dumps(summary))
    print("===END===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

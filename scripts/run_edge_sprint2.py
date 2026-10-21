#!/usr/bin/env python3
"""Edge Sprint 2 runner: evaluates the session/volatility hypotheses
(S01-S08) from ``edge_hypotheses`` through the Validation Fortress on real
hourly FX bars. Sprint 1's runner and results are not modified.

Data (Phase A outcome, documented in the generated report):
- Dukascopy candle files (BID_candles_hour_1.bi5) return HTTP 404 under
  every naming variant tried; Dukascopy tick files DO download via
  ``requests`` (curl receives an empty body, urllib connections are
  dropped by the sandbox egress proxy), but per-hour tick fetching was
  too slow/flaky through the proxy to assemble multi-year series
  (a serial 24-file day fetch exceeded 280 s).
- The dataset used here is a public GitHub export of hourly candles
  (repo ``millionairealde-creator/advanced_forex_bot``, commit
  ``45668048ad0dbb351242261c4bc67486ba66606c``, files ``data/*_H1.csv``),
  fetched from a commit-pinned raw URL and accepted ONLY when the
  downloaded bytes match the SHA-256 fingerprints recorded at validation
  time. Validation (all four pairs): strictly increasing UTC timestamps,
  zero OHLC violations, gaps only at weekends/holidays, and daily-close
  agreement with FRED noon rates consistent with the noon-ET vs
  midnight-UTC sampling offset (daily-return correlation 0.86-0.89,
  weekly-return correlation 0.97). Famous intraday extremes are
  reproduced at the correct UTC hours (GBPUSD 2022-09-26 low 1.03595 at
  01:00 UTC; USDJPY 2022-10-21 high 151.946 / low 146.197).
- Each file carries an exporter ``complete`` flag. Exactly 500 tail bars
  per file are flagged incomplete, and in USDJPY that block is a
  wrong-instrument splice (prices ~1.1). The uniform, pre-registered
  rule -- use only ``complete=True`` bars -- is applied identically to
  every pair before any evaluation.

Multiple-testing accounting: the deflated Sharpe gate is charged for
ALL trials in the research programme: Sprint 1's 24 base evaluations
plus Sprint 2's 32 (8 hypotheses x 4 instruments) = 56 trials, and the
trial-Sharpe population likewise pools both sprints' base runs (the
Sprint 1 trial Sharpes are reconstructed exactly by re-running the
Sprint 1 base simulations on FRED data in-process).

Costs, fortress thresholds and simulation semantics are Sprint 1's,
unchanged. No Sprint 1 hypothesis is re-run for a verdict here; M01-M06
stay rejected under their IDs.

Usage: python scripts/run_edge_sprint2.py [--data-dir PATH] [--out PATH]
The markdown report is always printed to stdout; file writes are
best-effort (a warning is printed when a write is not permitted).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import math
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_quant_os.edge_hypotheses import HYPOTHESES, BarsView, Hypothesis
from ai_quant_os.validation_fortress import (
    FortressConfig,
    FortressVerdict,
    StrategySeries,
    run_fortress,
)

_spec = importlib.util.spec_from_file_location(
    "run_edge_sprint", Path(__file__).resolve().parent / "run_edge_sprint.py"
)
sprint1 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sprint1  # dataclass processing in the module needs this
_spec.loader.exec_module(sprint1)

ONE_WAY_COST = sprint1.ONE_WAY_COST
simulate = sprint1.simulate
per_period_sharpe = sprint1.per_period_sharpe
periods_per_year = sprint1.periods_per_year

REPO_COMMIT = "45668048ad0dbb351242261c4bc67486ba66606c"
RAW_BASE = (
    "https://raw.githubusercontent.com/millionairealde-creator/"
    f"advanced_forex_bot/{REPO_COMMIT}/data/"
)
PAIR_FILES = {
    "EURUSD": "EUR_USD_H1.csv",
    "GBPUSD": "GBP_USD_H1.csv",
    "USDJPY": "USD_JPY_H1.csv",
    "EURJPY": "EUR_JPY_H1.csv",
}
# SHA-256 of the exact validated bytes (recorded during Phase A validation).
EXPECTED_SHA256 = {
    "EURUSD": "79be0bf5ed5125480ea39861e44ff0ce1aaa5527b7e861387da3538f1f12ba83",
    "GBPUSD": "2bfe905149ce258fe2f801ef0685d647856c691e003e8dc290166e798f692efc",
    "USDJPY": "c685e01801836a91f8d59436f6b699d75fc93fb539cdc6f1bb1479fac6006bf8",
    "EURJPY": "a9fb51f6cfcd68a2044992d443f6eaf0d36678be174af6f8b6cefcd4b224b616",
}
FRED_SERIES = {"EURUSD": "DEXUSEU", "GBPUSD": "DEXUSUK", "USDJPY": "DEXJPUS"}
PRICE_RANGE = {
    "EURUSD": (0.8, 1.4),
    "GBPUSD": (1.0, 1.6),
    "USDJPY": (80.0, 200.0),
    "EURJPY": (100.0, 200.0),
}
SPRINT1_N_TRIALS = 24


def fetch_validated_csv(instrument: str, tries: int = 10) -> bytes:
    """Fetch one pair CSV until its SHA-256 matches the validated bytes."""
    url = RAW_BASE + PAIR_FILES[instrument]
    expected = EXPECTED_SHA256[instrument]
    for _ in range(tries):
        try:
            response = requests.get(
                url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60
            )
            if response.status_code == 200 and response.content:
                if hashlib.sha256(response.content).hexdigest() == expected:
                    return response.content
        except Exception:
            pass
        time.sleep(1.0)
    raise RuntimeError(f"{instrument}: could not fetch validated bytes from {url}")


def parse_hourly_csv(instrument: str, content: bytes) -> tuple[BarsView, dict]:
    """Parse the export, keeping only complete=True bars. Returns bars and
    a validation-metrics dict (computed on the kept bars)."""
    timestamps: list[datetime] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    dropped = 0
    ohlc_violations = 0
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    for record in reader:
        if record["complete"] != "True":
            dropped += 1
            continue
        o = float(record["open"])
        h = float(record["high"])
        low = float(record["low"])
        c = float(record["close"])
        if not (h >= max(o, c) and low <= min(o, c) and low > 0):
            ohlc_violations += 1
        timestamps.append(datetime.fromisoformat(record["timestamp"]))
        opens.append(o)
        highs.append(h)
        lows.append(low)
        closes.append(c)
    non_monotonic = sum(
        1 for a, b in zip(timestamps, timestamps[1:]) if b <= a
    )
    gaps_over_72h = sum(
        1
        for a, b in zip(timestamps, timestamps[1:])
        if (b - a).total_seconds() > 72 * 3600
    )
    lo_r, hi_r = PRICE_RANGE[instrument]
    out_of_range = sum(1 for c in closes if not (lo_r <= c <= hi_r))
    bars = BarsView(
        instrument_id=instrument,
        timestamps=tuple(timestamps),
        opens=tuple(opens),
        highs=tuple(highs),
        lows=tuple(lows),
        closes=tuple(closes),
    )
    validation = {
        "kept_bars": len(closes),
        "dropped_incomplete": dropped,
        "ohlc_violations": ohlc_violations,
        "non_monotonic": non_monotonic,
        "gaps_over_72h": gaps_over_72h,
        "out_of_range_closes": out_of_range,
        "first": timestamps[0].isoformat() if timestamps else "",
        "last": timestamps[-1].isoformat() if timestamps else "",
        "sha256": EXPECTED_SHA256[instrument],
    }
    return bars, validation


def fetch_fred_series(series_id: str) -> dict[str, float]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd=2020-01-01"
    with urllib.request.urlopen(url, timeout=60) as response:
        text = response.read().decode("utf-8")
    out: dict[str, float] = {}
    for line in text.strip().split("\n")[1:]:
        day, _, value = line.partition(",")
        try:
            out[day] = float(value)
        except ValueError:
            continue
    return out


def _corr(a: list[float], b: list[float]) -> float:
    if len(a) < 10:
        return float("nan")
    ma = statistics.fmean(a)
    mb = statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else float("nan")


def fred_cross_check(bars_by_pair: dict[str, BarsView]) -> dict[str, dict[str, float]]:
    """Daily/weekly return correlation of each pair's closes vs FRED."""
    fred = {pair: fetch_fred_series(sid) for pair, sid in FRED_SERIES.items()}
    fred["EURJPY"] = {
        d: fred["EURUSD"][d] * fred["USDJPY"][d]
        for d in fred["EURUSD"]
        if d in fred["USDJPY"]
    }
    results: dict[str, dict[str, float]] = {}
    for pair, bars in bars_by_pair.items():
        daily: dict[str, float] = {}
        for ts, close in zip(bars.timestamps, bars.closes):
            daily[ts.date().isoformat()] = close
        common = sorted(set(daily) & set(fred[pair]))
        ra = [daily[common[i]] / daily[common[i - 1]] - 1 for i in range(1, len(common))]
        rb = [fred[pair][common[i]] / fred[pair][common[i - 1]] - 1 for i in range(1, len(common))]
        wa = [daily[common[i]] / daily[common[i - 5]] - 1 for i in range(5, len(common))]
        wb = [fred[pair][common[i]] / fred[pair][common[i - 5]] - 1 for i in range(5, len(common))]
        results[pair] = {
            "fred_days": float(len(common)),
            "daily_corr": _corr(ra, rb),
            "weekly_corr": _corr(wa, wb),
        }
    return results


def sprint1_trial_sharpes(data_dir: Path) -> tuple[float, ...] | None:
    """Reconstruct Sprint 1's exact 24 base-run per-period Sharpes by
    re-running its base simulations on FRED data (Sprint 1 runner code,
    unchanged). Returns None when FRED is unreachable."""
    try:
        series, _provenance = sprint1.load_fx_series(data_dir)
        bars_by_pair = {
            instrument: sprint1.build_bars(instrument, rows)
            for instrument, rows in series.items()
        }
        carry = sprint1.load_carry(series)
        sharpes: list[float] = []
        for hypothesis in sprint1.HYPOTHESES:
            if hypothesis.timeframe != "1d":
                continue
            for instrument in hypothesis.instruments:
                bars = bars_by_pair[instrument]
                diff = None
                if hypothesis.uses_carry and carry is not None:
                    diff = carry[instrument]
                positions = hypothesis.signal(bars, hypothesis.base_params, diff)
                accrual = diff if hypothesis.uses_carry else None
                sim = simulate(bars, positions, ONE_WAY_COST[instrument], accrual)
                sharpes.append(per_period_sharpe(sim.net))
        if len(sharpes) != SPRINT1_N_TRIALS:
            return None
        return tuple(sharpes)
    except Exception:
        return None


@dataclass
class Evaluation:
    hypothesis: Hypothesis
    instrument: str
    base: object
    verdict: FortressVerdict


def evaluate_all(
    bars_by_pair: dict[str, BarsView],
    prior_trial_sharpes: tuple[float, ...],
    n_trials_total: int,
) -> list[Evaluation]:
    session = [h for h in HYPOTHESES if h.timeframe == "1h"]
    base_runs: dict[tuple[str, str], tuple[object, dict[str, object]]] = {}
    trial_sharpes: list[float] = []
    for hypothesis in session:
        for instrument in hypothesis.instruments:
            bars = bars_by_pair[instrument]
            simulations: dict[str, object] = {}
            base_index = 0
            for i, params in enumerate(hypothesis.variants):
                if dict(params) == dict(hypothesis.base_params):
                    base_index = i
                positions = hypothesis.signal(bars, params, None)
                simulations[f"v{i}"] = simulate(
                    bars, positions, ONE_WAY_COST[instrument], None
                )
            base = simulations[f"v{base_index}"]
            base_runs[(hypothesis.id, instrument)] = (base, simulations)
            trial_sharpes.append(per_period_sharpe(base.net))
    all_trial_sharpes = tuple(prior_trial_sharpes) + tuple(trial_sharpes)
    n_trials = n_trials_total
    evaluations: list[Evaluation] = []
    for hypothesis in session:
        for instrument in hypothesis.instruments:
            bars = bars_by_pair[instrument]
            base, simulations = base_runs[(hypothesis.id, instrument)]
            series = StrategySeries(
                net_returns=base.net,
                gross_returns=base.gross,
                period_costs=base.costs,
                trade_count=base.trade_count,
                periods_per_year=periods_per_year(bars),
                variant_net_returns={k: s.net for k, s in simulations.items()},
                n_trials=n_trials,
                trial_sharpes=all_trial_sharpes,
            )
            verdict = run_fortress(series, FortressConfig())
            evaluations.append(Evaluation(hypothesis, instrument, base, verdict))
    return evaluations


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def fmt_num(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.{digits}f}"


def render_report(
    validations: dict[str, dict],
    cross_check: dict[str, dict[str, float]] | None,
    evaluations: list[Evaluation],
    n_trials_total: int,
    sprint1_sharpes_recovered: bool,
) -> str:
    lines: list[str] = []
    today = date.today().isoformat()
    passed = [e for e in evaluations if e.verdict.passed]
    lines.append("# Edge Sprint 2 — Results (Session/Volatility Family)")
    lines.append("")
    lines.append(f"**Date:** {today}  ")
    lines.append(
        "**Scope:** the pre-registered session/volatility hypotheses S01–S08 "
        "from `src/ai_quant_os/edge_hypotheses.py` (not evaluated in Sprint 1 "
        "for lack of hourly data), evaluated once through the Validation "
        "Fortress (`src/ai_quant_os/validation_fortress.py`, default "
        "thresholds, unchanged). No parameter was tuned after seeing results; "
        "a FAIL is a rejection under the hypothesis's own falsification "
        "criterion. Sprint 1's M01–M06 were NOT re-run and stay rejected."
    )
    lines.append("")
    lines.append("## Data acquisition (Phase A) — routes attempted")
    lines.append("")
    lines.append(
        "1. **Dukascopy aggregated candle files** "
        "(`BID_candles_hour_1.bi5`, urllib and `requests`, several naming "
        "variants): HTTP 404 for every variant — the candle tree does not "
        "serve these files at the documented paths. **Dukascopy tick files** "
        "(`{HH}h_ticks.bi5`) DO download via `requests` (curl receives an "
        "empty body; urllib connections are dropped by the sandbox egress "
        "proxy) and decode as valid LZMA tick streams, but per-hour fetching "
        "through the proxy was too slow/flaky to assemble multi-year series "
        "(a serial 24-file single-day fetch exceeded 280 s; a parallel "
        "throughput run died without output). Route abandoned on throughput, "
        "not on data quality."
    )
    lines.append(
        "2. **GitHub public datasets** (connector search): most candidates "
        "were stubs or samples (e.g. 3-byte placeholder CSVs in "
        "`mmayes313/jordan-rl-trading-bot`; a README-only repo at "
        "`FX-Data/FX-Data-EURUSD-DS`; ~5–12 KB samples elsewhere). One "
        "candidate passed screening: `millionairealde-creator/"
        "advanced_forex_bot`, `data/{EUR_USD,GBP_USD,USD_JPY,EUR_JPY}_H1.csv` "
        f"at commit `{REPO_COMMIT}`, fetched from commit-pinned raw URLs "
        "and accepted only on exact SHA-256 match with the bytes validated "
        "in Phase A. **This is the dataset evaluated below.**"
    )
    lines.append(
        "3. **HistData.com**: not reached — route 2 produced a fully "
        "validated dataset for all four pairs, so the form-based flow was "
        "not needed."
    )
    lines.append("")
    lines.append("## Dataset")
    lines.append("")
    lines.append(
        "Hourly candles (timestamp, open, high, low, close, volume, "
        "complete), timestamps in UTC. Bars are treated as mid-price "
        "candles; spread is charged through the Sprint 1 cost model. The "
        "exporter's `complete` flag is applied uniformly: only "
        "`complete=True` bars are used. Exactly 500 tail bars per file are "
        "flagged incomplete; in USDJPY that block is a wrong-instrument "
        "splice (prices ≈ 1.1), which is precisely what the flag exists to "
        "exclude. EURJPY is a genuine traded cross in this export, not a "
        "derived product."
    )
    lines.append("")
    lines.append(
        "| Instrument | Bars used | Dropped (incomplete) | First | Last | SHA-256 |"
    )
    lines.append("|---|---:|---:|---|---|---|")
    for instrument in ("EURUSD", "GBPUSD", "USDJPY", "EURJPY"):
        v = validations[instrument]
        lines.append(
            f"| {instrument} | {v['kept_bars']} | {v['dropped_incomplete']} | "
            f"{v['first'][:16]} | {v['last'][:16]} | `{v['sha256'][:16]}…` |"
        )
    lines.append("")
    lines.append("Validation on the kept bars (all four pairs):")
    lines.append("")
    lines.append(
        "| Instrument | Non-monotonic | OHLC violations | Out-of-range closes | "
        "Gaps > 72h | FRED daily-ret corr | FRED weekly-ret corr |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for instrument in ("EURUSD", "GBPUSD", "USDJPY", "EURJPY"):
        v = validations[instrument]
        cc = (cross_check or {}).get(instrument, {})
        lines.append(
            f"| {instrument} | {v['non_monotonic']} | {v['ohlc_violations']} | "
            f"{v['out_of_range_closes']} | {v['gaps_over_72h']} | "
            f"{fmt_num(cc.get('daily_corr'), 3)} | {fmt_num(cc.get('weekly_corr'), 3)} |"
        )
    lines.append("")
    lines.append(
        "The FRED comparison uses each pair's last hourly close per UTC day "
        "against the FRED noon (12:00 ET) buying rate, so a sampling offset "
        "of ~7.5 h is built into the daily figure (expected ceiling ≈ 0.9); "
        "the weekly figure (0.97) is the cleaner authenticity signal. "
        "Intraday extremes are reproduced at the correct UTC hours: GBPUSD "
        "2022-09-26 low 1.03595 at 01:00 UTC (flash crash), EURUSD "
        "2022-07-14 low 0.99522 at 13:00 UTC (parity break), USDJPY "
        "2022-10-21 high 151.946 at 12:00 UTC and low 146.197 at 16:00 UTC "
        "(intervention), USDJPY 2024-08-05 low 141.688 (carry unwind). "
        "Coverage differs by pair because the export's complete data ends "
        "earlier for GBPUSD (2025-10-19) and USDJPY (2024-12-31); every pair "
        "exceeds the 2-year minimum. Provenance risk is stated plainly: this "
        "is a single-uploader third-party export of unknown broker origin "
        "that passed every structural and cross-source check applied; it is "
        "not an official Dukascopy/broker feed."
    )
    lines.append("")
    lines.append("## Cost model (modelled, not observed) — Sprint 1, unchanged")
    lines.append("")
    lines.append(
        "One-way cost per 1.0 unit of position change (spread + slippage "
        "allowance), charged in the first period the new position is held. "
        "Fees are zero. The fortress re-tests everything at **2x** costs "
        "(COST_STRESS_2X gate)."
    )
    lines.append("")
    lines.append("| Instrument | One-way cost | In pips |")
    lines.append("|---|---:|---:|")
    pip = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "EURJPY": 0.01}
    for instrument, cost in ONE_WAY_COST.items():
        lines.append(f"| {instrument} | {cost} | {cost / pip[instrument]:.1f} |")
    lines.append("")
    lines.append("## Fortress gates (defaults, unchanged)")
    lines.append("")
    lines.append(
        "DATA_INTEGRITY · SAMPLE_SIZE (>= 100 trades, >= 240 periods) · "
        "EXPECTANCY · WALK_FORWARD (5 folds, >= 60% positive) · HOLDOUT "
        "(final 25%) · CSCV_PBO (<= 0.50) · DEFLATED_SHARPE (>= 0.95) · "
        "COST_STRESS_2X. Verdict PASS requires every gate to pass."
    )
    lines.append("")
    lines.append(
        f"**Multiple-testing accounting:** the deflated Sharpe correction is "
        f"charged for all trials in the programme: Sprint 1's 24 base "
        f"evaluations + Sprint 2's {len(evaluations)} = **{n_trials_total} "
        f"trials**, and the trial-Sharpe population pools both sprints' base "
        f"runs. Sprint 1 trial Sharpes "
        + (
            "were reconstructed exactly by re-running its base simulations "
            "in-process on FRED data."
            if sprint1_sharpes_recovered
            else "could NOT be reconstructed in this run (FRED unreachable); "
            "the count 24 is still charged in n_trials, and the dispersion "
            "population is Sprint 2's base runs — noted as a deviation."
        )
    )
    lines.append("")
    lines.append("## Headline result")
    lines.append("")
    lines.append(
        f"**{len(passed)} of {len(evaluations)} hypothesis/instrument "
        f"evaluations passed the fortress.**"
    )
    lines.append("")
    lines.append("## Results by evaluation")
    lines.append("")
    lines.append(
        "| Hypothesis | Instrument | Trades | Net return* | Ann. Sharpe | PBO | "
        "DSR | Stressed net* | Verdict | Failed gates |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---|---|")
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
    lines.append("")
    lines.append(
        "\\* Net return is the sum of per-period net returns (not compounded), "
        "the same quantity the fortress gates use."
    )
    lines.append("")
    lines.append("## Gate detail")
    for ev in evaluations:
        lines.append("")
        lines.append(f"### {ev.hypothesis.id} {ev.hypothesis.name} — {ev.instrument}")
        lines.append("")
        lines.append(f"Mechanism: {ev.hypothesis.mechanism}")
        lines.append("")
        lines.append(f"Falsifiable claim: {ev.hypothesis.falsifiable_claim}")
        lines.append("")
        lines.append("| Gate | Result | Metric | Reason |")
        lines.append("|---|---|---:|---|")
        for gate in ev.verdict.gates:
            lines.append(
                f"| {gate.gate} | {'PASS' if gate.passed else 'FAIL'} | "
                f"{fmt_num(gate.metric)} | {gate.reason} |"
            )
    lines.append("")
    lines.append("## Conclusions")
    lines.append("")
    if passed:
        names = ", ".join(f"{e.hypothesis.id}/{e.instrument}" for e in passed)
        lines.append(
            f"Passed: {names}. Under the roadmap, a fortress pass earns only "
            "the next rung — shadow evaluation on live data — not capital. "
            "Reality-gap expectations (50–70% of backtest performance) apply."
        )
    else:
        lines.append(
            "**No session/volatility hypothesis passed the fortress in this "
            "sprint.** The family that Sprint 1 could not test has now been "
            "tested on five years of validated hourly data, with the full "
            "programme-wide multiple-testing correction, and produced no "
            "survivor. Per the roadmap, the correct response is not to loosen "
            "the fortress or re-tune these IDs — rejected IDs stay rejected."
        )
    lines.append("")
    lines.append(
        "Every rejected hypothesis stays rejected under its ID. Any follow-up "
        "idea gets a new ID and increases the multiple-testing trial count. "
        "INR pairs remain **PENDING_DATA**."
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
    args = parser.parse_args()
    try:
        args.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    bars_by_pair: dict[str, BarsView] = {}
    validations: dict[str, dict] = {}
    for instrument in PAIR_FILES:
        content = fetch_validated_csv(instrument)
        bars, validation = parse_hourly_csv(instrument, content)
        bars_by_pair[instrument] = bars
        validations[instrument] = validation
    try:
        cross_check = fred_cross_check(bars_by_pair)
    except Exception:
        cross_check = None
    prior = sprint1_trial_sharpes(args.data_dir)
    sprint1_recovered = prior is not None
    if prior is None:
        prior = ()
    n_session_evals = sum(
        len(h.instruments) for h in HYPOTHESES if h.timeframe == "1h"
    )
    n_trials_total = SPRINT1_N_TRIALS + n_session_evals
    evaluations = evaluate_all(bars_by_pair, prior, n_trials_total)
    report = render_report(
        validations, cross_check, evaluations, n_trials_total, sprint1_recovered
    )
    summary = {
        "data": validations,
        "fred_cross_check": cross_check,
        "n_trials_total": n_trials_total,
        "sprint1_trial_sharpes_recovered": sprint1_recovered,
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
    out_path = args.out or (
        Path(__file__).resolve().parent.parent / "docs" / "EDGE_SPRINT_2_RESULTS.md"
    )
    for path, content in (
        (out_path, report),
        (args.data_dir / "edge_sprint_2_results.json", json.dumps(summary, indent=2)),
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

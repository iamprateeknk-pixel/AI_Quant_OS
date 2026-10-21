#!/usr/bin/env python3
"""Edge Sprint 1 runner: evaluates every hypothesis in ``edge_hypotheses``
through the Validation Fortress on real public FX data.

Data: FRED (Federal Reserve Economic Data) daily noon buying rates --
DEXUSEU (EURUSD), DEXUSUK (GBPUSD), DEXJPUS (USDJPY); EURJPY is derived as
EURUSD x USDJPY on the intersection of dates. Policy rates for the carry
hypotheses: DFF (USD), ECBDFR (EUR), IR3TIB01GBM156N (GBP, monthly),
IRSTCB01JPM156N (JPY, monthly). Series are fetched live from
fred.stlouisfed.org (no API key needed for the graph CSV endpoint) and, when
the environment permits file writes, cached under the data directory in the
repo's bar CSV import format for offline re-runs.

Modelling choices (also stated in the generated report):
- FRED noon rates are reference (mid) rates, not tradable OHLC. Each daily
  bar is represented with open = high = low = close = noon rate, volume 0.
  Range-based daily logic (M03 Donchian) therefore degenerates to a
  close-based breakout on this dataset.
- Transaction costs are modelled, not observed: a one-way cost per unit of
  position change (spread + slippage allowance, see ONE_WAY_COST), charged
  in the first period the new position is held. Fees are zero (spread-only
  retail spot convention). The fortress re-tests everything at 2x costs.
- Carry accrual (rate differential / 252 per day) is applied only to the
  hypotheses declared ``uses_carry``; the momentum hypotheses are evaluated
  on price return only, exactly as their pre-registered specifications say.
- Session/volatility hypotheses (timeframe 1h) are NOT evaluated in this
  sprint: no free intraday FX source was reachable from the build sandbox
  (Dukascopy and Stooq connections failed), and FRED/ECB publish no
  intraday FX. They are reported as NOT EVALUATED, never as passed.

Usage: python scripts/run_edge_sprint.py [--data-dir PATH] [--out PATH]
The markdown report is always printed to stdout; file writes are
best-effort (a warning is printed when a write is not permitted).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_quant_os.edge_hypotheses import HYPOTHESES, BarsView, Hypothesis
from ai_quant_os.validation_fortress import (
    FortressConfig,
    FortressVerdict,
    StrategySeries,
    run_fortress,
)

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
DATA_START = "2015-01-01"
FX_SERIES = {"EURUSD": "DEXUSEU", "GBPUSD": "DEXUSUK", "USDJPY": "DEXJPUS"}
RATE_SERIES = {
    "USD": "DFF",
    "EUR": "ECBDFR",
    "GBP": "IR3TIB01GBM156N",
    "JPY": "IRSTCB01JPM156N",
}
# One-way transaction cost per 1.0 unit of position change, in price units
# (spread + slippage allowance). Deliberately mid-range for retail spot FX;
# the fortress cost-stress gate re-tests at twice these values.
ONE_WAY_COST = {
    "EURUSD": 0.00008,  # 0.8 pip
    "GBPUSD": 0.00010,  # 1.0 pip
    "USDJPY": 0.008,  # 0.8 pip
    "EURJPY": 0.010,  # 1.0 pip
}
PAIR_CURRENCIES = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"),
    "EURJPY": ("EUR", "JPY"),
}


def fetch_fred(series_id: str, start: str = DATA_START) -> list[tuple[date, float]]:
    """Download one FRED series as (date, value) rows, skipping gaps."""
    url = f"{FRED_BASE}?id={series_id}&cosd={start}"
    with urllib.request.urlopen(url, timeout=60) as response:
        text = response.read().decode("utf-8")
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
    return rows


def canonical_csv(instrument: str, rows: list[tuple[date, float]]) -> str:
    """Render a series in the repo bar CSV import format (for fingerprints
    and the on-disk cache)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["instrument_id", "observed_at", "open", "high", "low", "close", "volume"])
    for day, value in rows:
        writer.writerow(
            [instrument, f"{day.isoformat()}T00:00:00+00:00", value, value, value, value, 0]
        )
    return buffer.getvalue()


def read_cache(path: Path, instrument: str) -> list[tuple[date, float]] | None:
    if not path.exists():
        return None
    rows: list[tuple[date, float]] = []
    with path.open(newline="") as handle:
        for record in csv.DictReader(handle):
            if record["instrument_id"] != instrument:
                continue
            day = datetime.fromisoformat(record["observed_at"]).date()
            rows.append((day, float(record["close"])))
    rows.sort()
    return rows or None


def load_fx_series(data_dir: Path) -> tuple[dict[str, list[tuple[date, float]]], str]:
    """Load the three FRED FX series (cache first, live fetch otherwise) and
    derive EURJPY. Returns (series by instrument, provenance note)."""
    series: dict[str, list[tuple[date, float]]] = {}
    notes: list[str] = []
    for instrument, fred_id in FX_SERIES.items():
        cache_path = data_dir / f"{instrument}_1d.csv"
        cached = read_cache(cache_path, instrument)
        if cached is not None:
            series[instrument] = cached
            notes.append(f"{instrument}: cache {cache_path.name}")
            continue
        rows = fetch_fred(fred_id)
        series[instrument] = rows
        notes.append(f"{instrument}: live FRED {fred_id}")
        try:
            cache_path.write_text(canonical_csv(instrument, rows), encoding="utf-8")
        except OSError:
            notes.append(f"{instrument}: cache write not permitted in this environment")
    eur = dict(series["EURUSD"])
    jpy = dict(series["USDJPY"])
    common = sorted(set(eur) & set(jpy))
    series["EURJPY"] = [(day, eur[day] * jpy[day]) for day in common]
    notes.append("EURJPY: derived as EURUSD x USDJPY on common dates")
    return series, "; ".join(notes)


def build_bars(instrument: str, rows: list[tuple[date, float]]) -> BarsView:
    timestamps = tuple(
        datetime(day.year, day.month, day.day, tzinfo=timezone.utc) for day, _ in rows
    )
    closes = tuple(value for _, value in rows)
    return BarsView(
        instrument_id=instrument,
        timestamps=timestamps,
        opens=closes,
        highs=closes,
        lows=closes,
        closes=closes,
    )


def forward_fill(days: list[date], observations: list[tuple[date, float]]) -> list[float]:
    """Last published value at or before each day (0.0 before the first)."""
    values: list[float] = []
    index = 0
    current = 0.0
    ordered = sorted(observations)
    for day in days:
        while index < len(ordered) and ordered[index][0] <= day:
            current = ordered[index][1]
            index += 1
        values.append(current)
    return values


def load_carry(
    series: dict[str, list[tuple[date, float]]],
) -> dict[str, tuple[float, ...]] | None:
    """Annual rate differential (base minus quote, decimal) per pair per day.
    Returns None when any rate series is unavailable."""
    rates: dict[str, list[tuple[date, float]]] = {}
    for currency, fred_id in RATE_SERIES.items():
        try:
            rates[currency] = fetch_fred(fred_id)
        except Exception:
            return None
        if not rates[currency]:
            return None
    carry: dict[str, tuple[float, ...]] = {}
    for instrument, rows in series.items():
        days = [day for day, _ in rows]
        base_ccy, quote_ccy = PAIR_CURRENCIES[instrument]
        base = forward_fill(days, rates[base_ccy])
        quote = forward_fill(days, rates[quote_ccy])
        carry[instrument] = tuple((b - q) / 100.0 for b, q in zip(base, quote))
    return carry


@dataclass
class Simulation:
    net: tuple[float, ...]
    gross: tuple[float, ...]
    costs: tuple[float, ...]
    trade_count: int


def simulate(
    bars: BarsView,
    positions: tuple[float, ...],
    one_way_cost_price: float,
    carry_diff: tuple[float, ...] | None,
) -> Simulation:
    """Turn target positions into per-period gross/cost/net returns.

    positions[t] is decided at the close of bar t and held over (t, t+1];
    its return and the cost of changing into it land in period t+1."""
    n = len(bars.closes)
    gross = [0.0] * n
    costs = [0.0] * n
    trades = 0
    previous = 0.0
    for t in range(n):
        pos = positions[t]
        if pos != 0.0 and (previous == 0.0 or (pos > 0) != (previous > 0)):
            trades += 1
        if t + 1 < n:
            gain = pos * (bars.closes[t + 1] / bars.closes[t] - 1.0)
            if carry_diff is not None:
                gain += pos * carry_diff[t] / 252.0
            gross[t + 1] = gain
            delta = abs(pos - previous)
            if delta > 0.0:
                costs[t + 1] = delta * one_way_cost_price / bars.closes[t]
        previous = pos
    net = tuple(g - c for g, c in zip(gross, costs))
    return Simulation(net, tuple(gross), tuple(costs), trades)


def per_period_sharpe(returns: tuple[float, ...]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0.0:
        return 0.0
    return mean / math.sqrt(variance)


def periods_per_year(bars: BarsView) -> float:
    span_days = (bars.timestamps[-1] - bars.timestamps[0]).days
    if span_days <= 0:
        return 252.0
    return len(bars.timestamps) / (span_days / 365.25)


@dataclass
class Evaluation:
    hypothesis: Hypothesis
    instrument: str
    base: Simulation
    verdict: FortressVerdict


def evaluate_all(
    bars_by_pair: dict[str, BarsView],
    carry: dict[str, tuple[float, ...]] | None,
) -> tuple[list[Evaluation], list[Hypothesis]]:
    """Run every daily hypothesis on every pair through the fortress.

    Two passes: base configurations first (their per-period Sharpes are the
    trial population for the deflated Sharpe correction), then the full
    fortress with each hypothesis's parameter-neighbourhood variants."""
    daily = [h for h in HYPOTHESES if h.timeframe == "1d"]
    skipped = [h for h in HYPOTHESES if h.timeframe != "1d"]
    base_runs: dict[tuple[str, str], tuple[Simulation, dict[str, Simulation]]] = {}
    trial_sharpes: list[float] = []
    for hypothesis in daily:
        for instrument in hypothesis.instruments:
            bars = bars_by_pair[instrument]
            diff = None
            if hypothesis.uses_carry and carry is not None:
                diff = carry[instrument]
            simulations: dict[str, Simulation] = {}
            base_index = 0
            for i, params in enumerate(hypothesis.variants):
                if dict(params) == dict(hypothesis.base_params):
                    base_index = i
                positions = hypothesis.signal(bars, params, diff)
                accrual = diff if hypothesis.uses_carry else None
                simulations[f"v{i}"] = simulate(bars, positions, ONE_WAY_COST[instrument], accrual)
            base = simulations[f"v{base_index}"]
            base_runs[(hypothesis.id, instrument)] = (base, simulations)
            trial_sharpes.append(per_period_sharpe(base.net))
    n_trials = len(trial_sharpes)
    evaluations: list[Evaluation] = []
    for hypothesis in daily:
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
                trial_sharpes=tuple(trial_sharpes),
            )
            verdict = run_fortress(series, FortressConfig())
            evaluations.append(Evaluation(hypothesis, instrument, base, verdict))
    return evaluations, skipped


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def fmt_num(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.{digits}f}"


def render_report(
    series: dict[str, list[tuple[date, float]]],
    provenance: str,
    carry_available: bool,
    evaluations: list[Evaluation],
    skipped: list[Hypothesis],
    fingerprints: dict[str, str],
) -> str:
    lines: list[str] = []
    today = date.today().isoformat()
    lines.append("# Edge Sprint 1 — Results")
    lines.append("")
    lines.append(f"**Date:** {today}  ")
    lines.append(
        "**Scope:** pre-registered hypotheses from `src/ai_quant_os/edge_hypotheses.py`, "
        "evaluated once through the Validation Fortress "
        "(`src/ai_quant_os/validation_fortress.py`, default thresholds). "
        "No parameter was tuned after seeing results; a FAIL is a rejection "
        "under the hypothesis's own falsification criterion."
    )
    lines.append("")
    lines.append("## Data")
    lines.append("")
    lines.append(
        "Source: FRED (Federal Reserve Economic Data), daily noon buying rates, "
        f"fetched from {FRED_BASE} starting {DATA_START}. {provenance}."
    )
    lines.append("")
    lines.append("| Instrument | FRED series | Bars | First | Last | SHA-256 of canonical CSV |")
    lines.append("|---|---|---:|---|---|---|")
    source_ids = {**FX_SERIES, "EURJPY": "derived EURUSD x USDJPY"}
    for instrument in ("EURUSD", "GBPUSD", "USDJPY", "EURJPY"):
        rows = series[instrument]
        lines.append(
            f"| {instrument} | {source_ids[instrument]} | {len(rows)} | "
            f"{rows[0][0].isoformat()} | {rows[-1][0].isoformat()} | "
            f"`{fingerprints[instrument][:16]}…` |"
        )
    lines.append("")
    lines.append(
        "Limitations, stated plainly: FRED noon rates are reference (mid) rates, "
        "not tradable OHLC — every bar here has open = high = low = close, so "
        "range-based daily logic (M03) degenerates to close-based breakouts. "
        "FRED observation dates are used as bar dates. Policy rates for carry "
        "are observation-dated and forward-filled; publication lags are ignored. "
        "INR pairs: **PENDING_DATA** — no free reliable USDINR reference series "
        "was obtainable, and none was fabricated."
    )
    lines.append("")
    if carry_available:
        carry_note = "available"
    else:
        carry_note = (
            "UNAVAILABLE — carry hypotheses were evaluated with a zero differential and fail closed"
        )
    lines.append(f"Carry rate data: {carry_note}.")
    lines.append("")
    lines.append("## Cost model (modelled, not observed)")
    lines.append("")
    lines.append(
        "One-way cost per 1.0 unit of position change (spread + slippage "
        "allowance), charged in the first period the new position is held. "
        "Fees are zero (spread-only retail spot convention). The fortress "
        "additionally requires the strategy to stay profitable at **2x** these "
        "costs (COST_STRESS_2X gate)."
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
        "(final 25%) · CSCV_PBO (<= 0.50) · DEFLATED_SHARPE (>= 0.95, "
        "corrected for all trials in this sprint) · COST_STRESS_2X. "
        "Verdict PASS requires every gate to pass."
    )
    lines.append("")
    passed = [e for e in evaluations if e.verdict.passed]
    lines.append("## Headline result")
    lines.append("")
    lines.append(
        f"**{len(passed)} of {len(evaluations)} hypothesis/instrument evaluations "
        f"passed the fortress.** {len(skipped)} hypotheses were not evaluated "
        "(see below). A low pass count is the expected outcome of an honest "
        "sprint — the roadmap's base case is that most hypotheses die here."
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
    lines.append("## Not evaluated")
    lines.append("")
    lines.append(
        "The session/volatility family (S01–S08) requires hourly bars. "
        "Dukascopy and Stooq were unreachable from the build sandbox and no "
        "other free intraday FX source was available, so these hypotheses "
        "were NOT EVALUATED in this sprint. Not evaluated is not passed: "
        "they remain untested until intraday data is obtained."
    )
    lines.append("")
    for hypothesis in skipped:
        lines.append(
            f"- **{hypothesis.id} {hypothesis.name}** ({hypothesis.timeframe}) — "
            "not evaluated: no hourly data."
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
            "**No hypothesis passed the fortress in this sprint.** That is a "
            "valid and valuable outcome: the daily carry/momentum family, on "
            "FRED reference data with realistic costs and full multiple-testing "
            "correction, produced no survivor. Per the roadmap, the correct "
            "response is not to loosen the fortress or re-tune these IDs — it "
            "is to test a different family/data (the session family on real "
            "intraday data, or INR-specific structure) with new hypothesis IDs."
        )
    lines.append("")
    lines.append(
        "Every rejected hypothesis stays rejected under its ID. Any follow-up "
        "idea gets a new ID and increases the multiple-testing trial count."
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
    series, provenance = load_fx_series(args.data_dir)
    bars_by_pair = {instrument: build_bars(instrument, rows) for instrument, rows in series.items()}
    carry = load_carry(series)
    evaluations, skipped = evaluate_all(bars_by_pair, carry)
    fingerprints = {
        instrument: hashlib.sha256(canonical_csv(instrument, rows).encode()).hexdigest()
        for instrument, rows in series.items()
    }
    report = render_report(
        series, provenance, carry is not None, evaluations, skipped, fingerprints
    )
    summary = {
        "data": {
            instrument: {
                "bars": len(rows),
                "first": rows[0][0].isoformat(),
                "last": rows[-1][0].isoformat(),
                "sha256": fingerprints[instrument],
            }
            for instrument, rows in series.items()
        },
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
        "not_evaluated": [h.id for h in skipped],
    }
    out_path = args.out or (
        Path(__file__).resolve().parent.parent / "docs" / "EDGE_SPRINT_1_RESULTS.md"
    )
    for path, content in (
        (out_path, report),
        (args.data_dir / "edge_sprint_1_results.json", json.dumps(summary, indent=2)),
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

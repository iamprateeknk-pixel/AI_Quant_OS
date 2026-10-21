from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util

import pytest

pytest.importorskip("nautilus_trader")

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.market_data import Bar, BarDataset


class RoundTrip:
    def __init__(self):
        self.seen = []

    def target_position(self, history):
        self.seen.append(tuple(history))
        return 1 if len(history) == 1 else 0


class StreamingRoundTrip:
    def __init__(self):
        self.closed_bars = 0
        self.decision_counts = []

    def on_bar(self, bar):
        assert bar.instrument_id == "EURUSD"
        self.closed_bars += 1

    def target_position(self):
        self.decision_counts.append(self.closed_bars)
        return 1 if self.closed_bars == 1 else 0


def dataset(identifier="EURUSD", start=None):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return BarDataset(
        Bar(
            identifier,
            start + timedelta(minutes=i),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(100),
        )
        for i, price in enumerate(("1.1", "1.2", "1.3"))
    )


def run(bars, strategy=None, costs=None, **kwargs):
    assert importlib.util.find_spec("ai_quant_os.nautilus_runtime") is not None
    from ai_quant_os.nautilus_runtime import run_nautilus_backtest

    return run_nautilus_backtest(
        bars,
        strategy or RoundTrip(),
        costs or BacktestCosts(Decimal(0), Decimal(0)),
        strategy_ref="fixture@1.0.0",
        **kwargs,
    )


def test_actual_native_engine_orders_fill_on_next_bar_without_lookahead():
    strategy = RoundTrip()
    result = run(dataset(), strategy)
    assert result.engine == "NAUTILUS_BACKTEST" and result.engine_version == "1.221.0"
    assert result.dataset_fingerprint == dataset().fingerprint
    assert result.native_order_count == 2
    assert [fill.side for fill in result.fills] == ["BUY", "SELL"]
    assert [fill.price for fill in result.fills] == [Decimal("1.2"), Decimal("1.3")]
    assert [fill.observed_at for fill in result.fills] == [
        bar.observed_at for bar in dataset().bars[1:]
    ]
    assert [len(rows) for rows in strategy.seen] == [1, 2]
    assert result.final_position == 0 and result.net_pnl == Decimal("0.1")


def test_native_bar_replay_supports_causal_streaming_strategy_without_history_tuples():
    strategy = StreamingRoundTrip()
    result = run(dataset(), strategy)
    assert result == run(dataset(), RoundTrip())
    assert strategy.decision_counts == [1, 2]
    assert strategy.closed_bars == 3


def test_native_bar_event_budget_rejects_before_building_quote_tape(monkeypatch):
    import ai_quant_os.nautilus_runtime as runtime

    monkeypatch.setattr(runtime, "MAX_NATIVE_REPLAY_EVENTS", 5, raising=False)
    monkeypatch.setattr(
        runtime,
        "_bar_quotes",
        lambda *args: pytest.fail("oversized dataset reached quote-tape construction"),
    )
    with pytest.raises(ValueError, match="event budget"):
        run(dataset())


def test_native_replay_is_reproducible_and_costs_are_charged_by_exchange():
    costs = BacktestCosts(Decimal(10), Decimal("0.1"))
    first = run(dataset(), costs=costs)
    assert first == run(dataset(), costs=costs)
    assert [fill.price for fill in first.fills] == [Decimal("1.2012"), Decimal("1.2987")]
    assert [fill.fee for fill in first.fills] == [Decimal("0.10")] * 2
    assert first.net_pnl == Decimal("-0.1025")
    assert first.pnl_method == "EXACT_FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK"
    assert first.native_realized_pnl == Decimal("-0.10")


@pytest.mark.parametrize(
    "identifier, currency",
    [("EURUSD", "USD"), ("GBPUSD", "USD"), ("USDJPY", "JPY"), ("EURJPY", "JPY")],
)
def test_canonical_instrument_registry_routes_only_to_simulated_venue(identifier, currency):
    result = run(dataset(identifier))
    assert result.instrument_id == identifier and result.pnl_currency == currency
    assert result.venue == "PAPER"
    assert result.net_pnl == Decimal("0.1")


def test_timezone_is_normalized_and_native_clock_keeps_microseconds():
    start = datetime(2026, 1, 1, 5, 30, 0, 123456, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    result = run(dataset(start=start))
    assert result.fills[0].observed_at == start.astimezone(timezone.utc) + timedelta(minutes=1)
    assert result.fills[0].observed_at.microsecond == 123456


def test_invalid_target_does_not_return_a_partial_success():
    class Bad:
        def target_position(self, history):
            return 2

    with pytest.raises(ValueError, match="target"):
        run(dataset(), Bad())


def test_precision_loss_and_unrepresentable_fees_are_rejected():
    bars = dataset().bars
    bar = Bar(
        "EURUSD",
        bars[0].observed_at,
        Decimal("1.000001"),
        Decimal("1.000001"),
        Decimal("1.000001"),
        Decimal("1.000001"),
        Decimal(1),
    )
    with pytest.raises(ValueError, match="precision"):
        run(BarDataset((bar,) + bars[1:]))
    with pytest.raises(ValueError, match="fee"):
        run(dataset(), costs=BacktestCosts(Decimal(0), Decimal("0.001")))


def test_unknown_and_mixed_instruments_cannot_reach_engine():
    with pytest.raises(ValueError, match="instrument"):
        run(dataset("UNKNOWN"))
    with pytest.raises(ValueError, match="one instrument"):
        run(BarDataset(dataset().bars + dataset("GBPUSD").bars))


def test_native_cli_produces_schema_bound_artifact(tmp_path):
    import json
    import subprocess
    import sys

    assert importlib.util.find_spec("ai_quant_os.nautilus_cli") is not None
    source = tmp_path / "bars.csv"
    source.write_text(
        "instrument_id,observed_at,open,high,low,close,volume\n"
        + "\n".join(
            f"EURUSD,{bar.observed_at.isoformat()},{bar.open},{bar.high},{bar.low},{bar.close},100"
            for bar in dataset().bars
        ),
        encoding="utf-8",
    )
    target = tmp_path / "result.json"
    command = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.nautilus_cli",
            str(source),
            "--strategy",
            "buy-and-hold",
            "--output",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    assert command.returncode == 0, command.stderr
    output = json.loads(target.read_text())
    assert output["engine"] == "NAUTILUS_BACKTEST"
    assert output["strategy_ref"] == "buy-and-hold@1.0.0"
    assert output["native_order_count"] == 1
    assert Decimal(output["net_pnl"]) == Decimal("0.1")
    assert output["source_sha256"]


@pytest.mark.parametrize(
    "dataset_format, header, first_row, extra_row",
    [
        (
            "bars",
            "instrument_id,observed_at,open,high,low,close,volume\n",
            "EURUSD,2026-01-01T00:00:00+00:00,1.09,1.11,1.08,1.10,100\n",
            "EURUSD,2026-01-01T00:01:00+00:00,not-a-price,1.11,1.08,1.10,100\n",
        ),
        (
            "ticks",
            "instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n",
            "EURUSD,fixture,1,2026-01-01T00:00:00+00:00,1.09,1.10,100,100\n",
            "EURUSD,fixture,2,2026-01-01T00:00:01+00:00,1.09,1.10,100,100\n"
            "EURUSD,fixture,3,2026-01-01T00:00:02+00:00,not-a-price,1.10,100,100\n",
        ),
    ],
)
def test_native_cli_enforces_row_budget_during_csv_parse(
    tmp_path, monkeypatch, capsys, dataset_format, header, first_row, extra_row
):
    import sys

    from ai_quant_os import nautilus_cli

    monkeypatch.setattr(nautilus_cli, "MAX_NATIVE_REPLAY_EVENTS", 2)
    source = tmp_path / "oversized.csv"
    source.write_text(header + first_row + extra_row, encoding="utf-8")
    output = tmp_path / "result.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ai-quant-backtest",
            str(source),
            "--format",
            dataset_format,
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        nautilus_cli.main()
    assert "CSV row limit exceeded" in capsys.readouterr().err
    assert not output.exists()


def test_performance_baseline_runs_real_replays_and_checks_reproducibility(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).parents[1] / "scripts" / "benchmark_nautilus.py"
    assert script.exists()
    target = tmp_path / "benchmark.json"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--bars",
            "100",
            "--iterations",
            "2",
            "--output",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(target.read_text())
    assert report["engine_version"] == "1.221.0" and report["bar_count"] == 100
    assert report["reproducible"] is True and report["iterations"] == 2
    assert report["native_order_count"] > 0
    assert all(value > 0 for value in report["elapsed_ms"])


def test_native_result_does_not_depend_on_callers_decimal_precision():
    from decimal import Inexact, localcontext

    expected = run(dataset(), costs=BacktestCosts(Decimal(10), Decimal("0.1")))
    with localcontext() as context:
        context.prec = 4
        assert run(dataset(), costs=BacktestCosts(Decimal(10), Decimal("0.1"))) == expected

    class Fractional:
        def target_position(self, history):
            Decimal(1) / 3
            return 1

    fractional = run(dataset(), Fractional())
    with localcontext() as context:
        context.traps[Inexact] = True
        assert run(dataset(), Fractional()) == fractional

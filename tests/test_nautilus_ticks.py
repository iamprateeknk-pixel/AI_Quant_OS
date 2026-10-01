from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import subprocess
import sys

import pytest

pytest.importorskip("nautilus_trader")

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.tick_data import Tick, TickDataset


def ticks():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return TickDataset(
        Tick(
            "EURUSD",
            "fixture",
            i,
            start + timedelta(seconds=i),
            Decimal(bid),
            Decimal(ask),
            Decimal(100),
            Decimal(100),
        )
        for i, (bid, ask) in enumerate(
            (("1.10", "1.11"), ("1.20", "1.21"), ("1.30", "1.31"), ("1.40", "1.41"))
        )
    )


class Flip:
    def __init__(self):
        self.seen = []

    def target_position(self, history):
        self.seen.append(history)
        return (1, -1, 0)[len(history) - 1]


def run(dataset, plugin=None, **kwargs):
    from ai_quant_os.nautilus_runtime import run_nautilus_tick_backtest

    return run_nautilus_tick_backtest(
        dataset,
        plugin or Flip(),
        BacktestCosts(Decimal(0), Decimal("0.10")),
        strategy_ref="tick-fixture@1.0.0",
        **kwargs,
    )


def test_recorded_spreads_execute_next_tick_and_reconcile_short_reversal():
    plugin = Flip()
    result = run(ticks(), plugin)
    assert [len(rows) for rows in plugin.seen] == [1, 2, 3]
    assert all(rows == ticks().ticks[:i] for i, rows in enumerate(plugin.seen, 1))
    assert [f.price for f in result.fills] == [Decimal("1.21"), Decimal("1.30"), Decimal("1.41")]
    assert [f.quantity for f in result.fills] == [1, 2, 1]
    assert [f.side for f in result.fills] == ["BUY", "SELL", "BUY"]
    assert [f.observed_at for f in result.fills] == [t.observed_at for t in ticks().ticks[1:]]
    assert result.final_position == 0 and result.net_pnl == Decimal("-0.32")
    assert result.replay_model == "RECORDED_QUOTE_TICKS_V1"
    assert result.dataset_fingerprint == ticks().fingerprint
    assert result == run(ticks())


def test_open_short_is_marked_at_final_quote_midpoint():
    class Short:
        def target_position(self, history):
            return -1

    result = run(ticks(), Short(), quantity=2)
    assert result.final_position == -2
    assert result.net_pnl == Decimal("-0.51")


@pytest.mark.parametrize(
    "change, message",
    [
        ({"bid_size": Decimal(0)}, "liquidity"),
        ({"ask_size": Decimal("1.5")}, "size"),
        ({"bid": Decimal("1.100001")}, "precision"),
        ({"source_id": "other"}, "source"),
        ({"observed_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}, "timestamp"),
    ],
)
def test_ambiguous_or_unrepresentable_ticks_fail_closed(change, message):
    from dataclasses import replace

    rows = ticks().ticks
    changed = TickDataset((rows[0], replace(rows[1], **change), *rows[2:]))
    with pytest.raises(ValueError, match=message):
        run(changed)


def test_archive_verified_replay_and_cli_use_same_native_result(tmp_path):
    pytest.importorskip("pyarrow")
    from ai_quant_os.tick_archive import TickArchive
    from ai_quant_os.tick_data import import_ticks_csv

    path = tmp_path / "ticks.csv"
    path.write_text(
        "instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n"
        + "\n".join(
            f"{t.instrument_id},{t.source_id},{t.sequence},{t.observed_at.isoformat()},"
            f"{t.bid},{t.ask},{t.bid_size},{t.ask_size}"
            for t in ticks().ticks
        ),
        encoding="utf-8",
    )
    archive = TickArchive(tmp_path / "archive")
    manifest = archive.ingest_csv(path, source_id="fixture", ingested_at=datetime.now(timezone.utc))
    assert run(archive.replay(manifest.archive_id)) == run(import_ticks_csv(path))
    output = tmp_path / "result.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.nautilus_cli",
            str(path),
            "--format",
            "ticks",
            "--strategy",
            "tick-momentum",
            "--fee",
            "0.10",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    from ai_quant_os.tick_strategy import TickMomentum

    result = run(archive.replay(manifest.archive_id), TickMomentum())
    report = json.loads(output.read_text())
    assert report["dataset_fingerprint"] == result.dataset_fingerprint
    assert Decimal(report["net_pnl"]) == result.net_pnl
    assert report["fills"] == [
        {
            "observed_at": f.observed_at.isoformat(),
            "side": f.side,
            "price": str(f.price),
            "quantity": str(f.quantity),
            "fee": str(f.fee),
        }
        for f in result.fills
    ]


@pytest.mark.parametrize("target", [2, True, Decimal(1)])
def test_tick_invalid_plugin_target_cannot_return_success(target):
    class Invalid:
        def target_position(self, history):
            return target

    with pytest.raises(ValueError, match="target"):
        run(ticks(), Invalid())


def test_recorded_spread_cannot_be_replaced_with_synthetic_slippage():
    from ai_quant_os.nautilus_runtime import run_nautilus_tick_backtest

    with pytest.raises(ValueError, match="zero synthetic slippage"):
        run_nautilus_tick_backtest(
            ticks(), Flip(), BacktestCosts(Decimal(1), Decimal(0)), strategy_ref="fixture@1.0.0"
        )


def test_tick_momentum_is_causal_and_supports_three_targets():
    from ai_quant_os.tick_strategy import TickMomentum

    plugin = TickMomentum()
    assert plugin.target_position(()) == 0
    assert plugin.target_position(ticks().ticks[:1]) == 0
    assert plugin.target_position(ticks().ticks[:2]) == 1
    assert plugin.target_position(tuple(reversed(ticks().ticks[:2]))) == -1
    assert plugin.target_position((ticks().ticks[0], ticks().ticks[0])) == 0

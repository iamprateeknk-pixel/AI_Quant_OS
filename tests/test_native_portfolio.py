from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytest.importorskip("nautilus_trader")


def inputs(quantity=10, identifiers=("EURUSD", "GBPUSD", "USDJPY", "EURJPY")):
    from ai_quant_os.native_portfolio import PortfolioInput
    from ai_quant_os.market_data import Bar, BarDataset
    from ai_quant_os.strategy_contracts import StrategyGenome

    genome = StrategyGenome("moving-average-directional@1.0.0", "BAR", (("fast", 1), ("slow", 2)))
    values = {
        "EURUSD": ("1.1", "1.2", "1.3", "1.2", "1.1", "1.2", "1.3", "1.2"),
        "GBPUSD": ("1.3", "1.4", "1.5", "1.4", "1.3", "1.4", "1.5", "1.4"),
        "USDJPY": ("150", "151", "152", "151", "150", "151", "152", "151"),
        "EURJPY": ("165", "181", "197", "181", "165", "181", "197", "181"),
    }
    return tuple(
        PortfolioInput(
            BarDataset(
                Bar(
                    identifier,
                    datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
                    *(Decimal(price) for _ in range(4)),
                    Decimal(100),
                )
                for i, price in enumerate(values[identifier])
            ),
            genome,
            quantity,
            Decimal(10),
        )
        for identifier in identifiers
    )


def test_four_pairs_share_one_native_engine_and_actual_usd_fees(tmp_path, monkeypatch):
    import ai_quant_os.native_portfolio as portfolio
    from ai_quant_os.strategy_registry import StrategyRegistry

    source = inputs()
    original, engines = portfolio.BacktestEngine, []

    def observed(*args, **kwargs):
        engine = original(*args, **kwargs)
        engines.append(engine)
        return engine

    monkeypatch.setattr(portfolio, "BacktestEngine", observed)
    report = portfolio.run_native_portfolio(
        source, StrategyRegistry(tmp_path / "sources"), fee_per_order_usd=Decimal(".10")
    )
    assert len(engines) == 1
    assert report.engine_version == "1.221.0"
    assert report.replay_model == "SYNCHRONIZED_BAR_PORTFOLIO_USD_V1"
    assert report.promotion_allowed is False
    assert report.native_order_count == 12
    assert report.total_fees_usd == Decimal("1.20")
    assert [leg.instrument_id for leg in report.legs] == sorted(
        row.dataset.bars[0].instrument_id for row in source
    )
    assert all(leg.final_position == 10 for leg in report.legs)
    assert all([f.quantity for f in leg.fills] == [10, 20, 20] for leg in report.legs)
    assert all(leg.fee_currency == "USD" for leg in report.legs)
    assert report.net_pnl_usd == sum(leg.gross_pnl_usd - leg.fees_usd for leg in report.legs)
    assert report == portfolio.run_native_portfolio(
        tuple(reversed(source)),
        StrategyRegistry(tmp_path / "replay"),
        fee_per_order_usd=Decimal(".10"),
    )
    portfolio.verify_portfolio_report(report)


def test_shared_collateral_rejects_basket_that_each_separate_leg_can_afford(tmp_path):
    from ai_quant_os.native_portfolio import run_native_portfolio
    from ai_quant_os.strategy_registry import StrategyRegistry

    # Each leg costs less than 20k USD, but their simultaneous total exceeds it.
    with pytest.raises(ValueError, match="Native.*(denied|rejected)"):
        run_native_portfolio(
            inputs(10000, ("EURUSD", "GBPUSD")),
            StrategyRegistry(tmp_path / "sources"),
            initial_balance_usd=Decimal(20000),
        )


def test_basket_refuses_missing_fx_asynchronous_or_repeated_instruments(tmp_path):
    from dataclasses import replace
    from ai_quant_os.native_portfolio import run_native_portfolio
    from ai_quant_os.market_data import BarDataset
    from ai_quant_os.strategy_registry import StrategyRegistry

    source = inputs()
    registry = StrategyRegistry(tmp_path / "sources")
    with pytest.raises(ValueError, match="USDJPY"):
        run_native_portfolio((source[0], source[3]), registry)
    with pytest.raises(ValueError, match="unique"):
        run_native_portfolio((source[0], source[0]), registry)
    mismatch = replace(source[1], dataset=BarDataset(source[1].dataset.bars[:-1]))
    with pytest.raises(ValueError, match="synchronized"):
        run_native_portfolio((source[0], mismatch), registry)


def test_basket_retained_source_cash_and_valuation_cannot_be_altered(tmp_path):
    from dataclasses import replace
    from ai_quant_os.native_portfolio import run_native_portfolio, verify_portfolio_report
    from ai_quant_os.strategy_registry import StrategyRegistry

    report = run_native_portfolio(
        inputs(), StrategyRegistry(tmp_path / "sources"), fee_per_order_usd=Decimal(".10")
    )
    with pytest.raises(ValueError):
        verify_portfolio_report(replace(report, net_pnl_usd=Decimal(999)))
    first = report.legs[0]
    with pytest.raises(ValueError):
        verify_portfolio_report(
            replace(report, legs=(replace(first, final_position=Decimal(999)), *report.legs[1:]))
        )


def test_portfolio_command_preserves_inputs_lineage_and_immutable_export(tmp_path):
    import json
    import subprocess
    import sys
    from hashlib import sha256

    source = inputs()
    paths, entries = [], []
    for leg in source:
        path = tmp_path / (leg.dataset.bars[0].instrument_id + ".csv")
        path.write_text(
            "instrument_id,observed_at,open,high,low,close,volume\n"
            + "\n".join(
                f"{b.instrument_id},{b.observed_at.isoformat()},{b.open},{b.high},{b.low},{b.close},{b.volume}"
                for b in leg.dataset.bars
            )
        )
        paths.append(path)
        entries.append(
            {
                "csv": path.name,
                "genome": {
                    "strategy_ref": leg.genome.strategy_ref,
                    "input_kind": "BAR",
                    "parameters": [["fast", 1], ["slow", 2]],
                },
                "quantity": 10,
                "slippage_bps": "10",
            }
        )
    plan = tmp_path / "plan.json"
    payload = {"schema_version": "1.0", "legs": entries, "fee_per_order_usd": "0.10"}
    plan.write_text(json.dumps(payload))
    output = tmp_path / "result.json"
    arguments = [
        sys.executable,
        "-m",
        "ai_quant_os.portfolio_backtest_cli",
        str(plan),
        "--output",
        str(output),
    ]
    proc = subprocess.run(arguments, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    encoded = output.read_bytes()
    saved = json.loads(encoded)
    assert saved["report"]["native_order_count"] == 12
    assert {row["source_sha256"] for row in saved["source_lineage"]} == {
        sha256(path.read_bytes()).hexdigest() for path in paths
    }
    assert subprocess.run(arguments, capture_output=True).returncode == 0
    assert output.read_bytes() == encoded
    payload["fee_per_order_usd"] = "0.20"
    plan.write_text(json.dumps(payload))
    refusal = subprocess.run(arguments, capture_output=True, text=True)
    assert refusal.returncode != 0 and "immutable" in refusal.stderr
    assert output.read_bytes() == encoded
    refusal = subprocess.run([*arguments[:-1], str(paths[0])], capture_output=True, text=True)
    assert refusal.returncode != 0 and "overwrite" in refusal.stderr


@pytest.mark.parametrize("quantity", [True, 500001])
def test_portfolio_invalid_quantity_is_refused_before_native_engine(
    tmp_path, monkeypatch, quantity
):
    from dataclasses import replace
    import ai_quant_os.native_portfolio as portfolio
    from ai_quant_os.strategy_registry import StrategyRegistry

    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid portfolio must not create an engine")

    monkeypatch.setattr(portfolio, "BacktestEngine", forbidden)
    source = inputs()
    with pytest.raises(ValueError, match="quantity"):
        portfolio.run_native_portfolio(
            (replace(source[0], quantity=quantity), *source[1:]),
            StrategyRegistry(tmp_path / "sources"),
        )


def test_portfolio_usd_conversion_mark_and_caller_precision_reconcile(tmp_path):
    from decimal import localcontext
    from ai_quant_os.native_portfolio import run_native_portfolio
    from ai_quant_os.strategy_registry import StrategyRegistry

    source = inputs()
    expected = run_native_portfolio(
        source, StrategyRegistry(tmp_path / "sources"), fee_per_order_usd=Decimal(".10")
    )
    with localcontext() as context:
        context.prec = 2
        actual = run_native_portfolio(
            source, StrategyRegistry(tmp_path / "other"), fee_per_order_usd=Decimal(".10")
        )
    assert actual == expected
    usd_jpy = next(row for row in actual.legs if row.instrument_id == "USDJPY")
    for leg in actual.legs:
        if leg.quote_currency == "JPY":
            with localcontext() as context:
                context.prec = 28
                assert leg.gross_pnl_usd == leg.gross_pnl_quote / Decimal("150.849")
    assert usd_jpy.gross_pnl_quote < 0


def test_current_fx_open_is_available_before_first_cross_pair_decision(tmp_path):
    from dataclasses import replace
    from ai_quant_os.native_portfolio import PortfolioInput, run_native_portfolio
    from ai_quant_os.market_data import BarDataset
    from ai_quant_os.strategy_contracts import StrategyGenome
    from ai_quant_os.strategy_registry import StrategyRegistry

    base = inputs(1, ("EURJPY", "USDJPY"))
    selected = []
    for source, prices, quantity in zip(base, ((100, 180), (100, 150)), (100, 1), strict=True):
        bars = tuple(
            replace(
                row,
                open=Decimal(price),
                high=Decimal(price),
                low=Decimal(price),
                close=Decimal(price),
            )
            for row, price in zip(source.dataset.bars[:2], prices, strict=True)
        )
        selected.append(
            PortfolioInput(BarDataset(bars), StrategyGenome("buy-and-hold@1.0.0", "BAR"), quantity)
        )
    report = run_native_portfolio(
        tuple(selected), StrategyRegistry(tmp_path / "sources"), initial_balance_usd=Decimal(150)
    )
    assert report.native_order_count == 2
    assert report.net_pnl_usd == 0
    # The current USDJPY open makes gross collateral 121 USD; the prior rate would reject it.
    assert [row.final_position for row in report.legs] == [100, 1]


def test_saved_native_portfolio_replays_actual_shared_engine(tmp_path):
    import json
    from pathlib import Path
    from pydantic import TypeAdapter
    from ai_quant_os.native_portfolio import (
        PortfolioReport,
        PortfolioInput,
        run_native_portfolio,
        verify_portfolio_report,
    )
    from ai_quant_os.market_data import BarDataset
    from ai_quant_os.strategy_registry import StrategyRegistry

    payload = json.loads(
        (Path(__file__).parents[1] / "docs/evaluations/native-portfolio-baseline.json").read_text()
    )
    saved = TypeAdapter(PortfolioReport).validate_python(payload["report"])
    verify_portfolio_report(saved)
    source = tuple(
        PortfolioInput(
            BarDataset(leg.source_bars), leg.binding.genome, leg.quantity, leg.slippage_bps
        )
        for leg in saved.legs
    )
    actual = run_native_portfolio(
        source,
        StrategyRegistry(tmp_path / "sources"),
        fee_per_order_usd=saved.fee_per_order_usd,
        initial_balance_usd=saved.initial_balance_usd,
    )
    assert actual == saved

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import pytest


def cash_fills():
    from ai_quant_os.monte_carlo import CashFill

    return tuple(
        CashFill(side, Decimal(qty), Decimal(price), Decimal(fee))
        for side, qty, price, fee in (
            ("BUY", "10", "1", ".1"),
            ("SELL", "20", "1.2", ".3"),
            ("BUY", "10", "1.1", ".1"),
        )
    )


def test_equal_reversal_half_fee_policy_reconciles_both_closed_cycles():
    from ai_quant_os.monte_carlo import closed_trade_series

    sample = closed_trade_series(cash_fills(), reversal_fee_policy="EQUAL_SIZE_HALF_FEE_V1")
    assert sample.net_pnls == (Decimal("1.75"), Decimal(".75"))
    assert sample.open_position == 0
    with localcontext() as context:
        context.prec = 2
        assert (
            closed_trade_series(cash_fills(), reversal_fee_policy="EQUAL_SIZE_HALF_FEE_V1")
            == sample
        )


def test_fee_policy_preserves_open_leg_and_rejects_unequal_or_unknown_reversal():
    from ai_quant_os.monte_carlo import closed_trade_series

    partial = closed_trade_series(cash_fills()[:2], reversal_fee_policy="EQUAL_SIZE_HALF_FEE_V1")
    assert partial.net_pnls == (Decimal("1.75"),)
    assert partial.open_position == -10
    with pytest.raises(ValueError, match="equal"):
        closed_trade_series(
            (cash_fills()[0], replace(cash_fills()[1], quantity=Decimal(25))),
            reversal_fee_policy="EQUAL_SIZE_HALF_FEE_V1",
        )
    with pytest.raises(ValueError, match="policy"):
        closed_trade_series(cash_fills(), reversal_fee_policy="invented")
    with pytest.raises(ValueError, match="reversal"):
        closed_trade_series(cash_fills())


def native_inputs():
    from ai_quant_os.market_data import BarDataset, import_bars_csv
    from ai_quant_os.strategy_contracts import StrategyGenome
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os.adversarial import AdversarialSpec
    from test_research_pipeline import plan

    root = Path(__file__).parent / "fixtures"
    development = import_bars_csv(root / "research_mc_development.csv")
    original = import_bars_csv(root / "research_mc_holdout.csv")
    last = original.bars[-1]
    holdout = BarDataset(
        (
            *original.bars,
            *(
                replace(
                    last,
                    observed_at=last.observed_at + timedelta(minutes=i),
                    open=last.close,
                    high=last.close,
                    low=last.close,
                )
                for i in (1, 2)
            ),
        )
    )
    declaration = plan(
        development,
        holdout,
        experiment_id="directional-statistics",
        candidates=(
            StrategyGenome("moving-average-directional@1.0.0", "BAR", (("fast", 1), ("slow", 2))),
        ),
        train_bars=20,
        validation_bars=20,
        schema_version="1.2",
        monte_carlo_spec=MonteCarloSpec(
            iterations=100, method="CIRCULAR_EQUAL_REVERSAL_HALF_FEE_BLOCKS_SHA256_V1"
        ),
        adversarial_spec=AdversarialSpec(maximum_loss=Decimal(100)),
    )
    return declaration, development, holdout


def run_native(tmp_path):
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    declaration, development, holdout = native_inputs()
    store = ResearchStore(tmp_path / "research.sqlite3")
    report = run_research_experiment(
        declaration, development, holdout, StrategyRegistry(tmp_path / "strategies"), store
    )
    return report, store


def test_native_directional_statistics_and_adversarial_evidence_reconcile(tmp_path):
    pytest.importorskip("nautilus_trader")
    from ai_quant_os.research_store import ResearchStore, encode_research
    from ai_quant_os.monte_carlo import CashFill, closed_trade_series

    report, store = run_native(tmp_path)
    assert report.outcome == "VALIDATION_INCOMPLETE"
    assert report.promotion_allowed is False
    assert report.holdout.result.final_position == 0
    assert any(fill.quantity == 200 for fill in report.holdout.result.fills)
    sample = closed_trade_series(
        tuple(CashFill(f.side, f.quantity, f.price, f.fee) for f in report.holdout.result.fills),
        reversal_fee_policy="EQUAL_SIZE_HALF_FEE_V1",
    )
    assert sum(sample.net_pnls) == report.holdout.result.net_pnl
    assert report.monte_carlo.result.closed_trade_count >= 20
    assert report.monte_carlo.result.state == "PASS"
    assert report.adversarial.state == "PASS"
    assert {gate.name for gate in report.gates if gate.state == "PENDING"} == {
        "SANDBOX",
        "INDEPENDENT_REVIEW",
        "SHADOW",
    }
    assert encode_research(
        ResearchStore(store.path).get(report.plan.experiment_id)
    ) == encode_research(report)
    with pytest.raises(ValueError, match="Monte Carlo"):
        replace(
            report,
            monte_carlo=replace(
                report.monte_carlo,
                result=replace(report.monte_carlo.result, median_pnl=Decimal(999)),
            ),
        )
    scenario = report.adversarial.scenarios[0]
    fake = replace(scenario.run, result=replace(scenario.run.result, final_position=Decimal(999)))
    with pytest.raises(ValueError, match="Adversarial"):
        replace(
            report,
            adversarial=replace(
                report.adversarial,
                scenarios=(replace(scenario, run=fake), *report.adversarial.scenarios[1:]),
            ),
            attempts=tuple(fake if row == scenario.run else row for row in report.attempts),
        )


def test_directional_result_rejects_undeclared_native_fee_policy(tmp_path):
    pytest.importorskip("nautilus_trader")
    from ai_quant_os.research_monte_carlo import evaluate_native_monte_carlo
    from ai_quant_os.monte_carlo import MonteCarloSpec

    report, _ = run_native(tmp_path)
    with pytest.raises(ValueError, match="fee"):
        evaluate_native_monte_carlo(report.holdout, MonteCarloSpec(iterations=100))


@pytest.mark.parametrize("first_side", ["BUY", "SELL"])
def test_partial_exits_and_reversal_fee_allocations_sum_to_entire_cash_ledger(first_side):
    from ai_quant_os.monte_carlo import CashFill, closed_trade_series

    opposite = "SELL" if first_side == "BUY" else "BUY"
    fills = tuple(
        CashFill(side, Decimal(qty), Decimal(price), Decimal(fee))
        for side, qty, price, fee in (
            (first_side, 20, "1.0", ".13"),
            (opposite, 10, "1.1", ".07"),
            (opposite, 20, "1.2", ".01"),
            (first_side, 10, "1.1", ".17"),
        )
    )
    sample = closed_trade_series(fills, reversal_fee_policy="EQUAL_SIZE_HALF_FEE_V1")
    cash = sum(
        (-f.price * f.quantity if f.side == "BUY" else f.price * f.quantity) - f.fee for f in fills
    )
    assert sum(sample.net_pnls) == cash
    assert sample.open_position == 0
    assert len(sample.net_pnls) == 2


def test_directional_resampling_budget_refuses_before_holdout_reservation(tmp_path, monkeypatch):
    pytest.importorskip("nautilus_trader")
    from ai_quant_os.market_data import BarDataset
    from ai_quant_os.research_contracts import ResearchDatasetScope
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    declaration, development, source = native_inputs()
    # 260 observations can close a trade on each reversal; 2,000 draws exceed 500k.
    source = BarDataset(source.bars[:260])
    declaration = replace(
        declaration,
        holdout_scope=ResearchDatasetScope(
            "EURUSD", source.bars[0].observed_at, source.bars[-1].observed_at, source.fingerprint
        ),
        holdout_fingerprint=source.fingerprint,
        monte_carlo_spec=replace(declaration.monte_carlo_spec, iterations=2000),
    )
    store = ResearchStore(tmp_path / "research.sqlite3")

    def forbidden(*args):
        raise AssertionError("Oversized computation must not reserve a holdout")

    monkeypatch.setattr(store, "reserve", forbidden)
    with pytest.raises(ValueError, match="computation budget"):
        run_research_experiment(
            declaration, development, source, StrategyRegistry(tmp_path / "strategies"), store
        )


def test_command_requires_and_retains_declared_directional_fee_method(tmp_path):
    pytest.importorskip("nautilus_trader")
    pytest.importorskip("pyarrow")
    import json
    import subprocess
    import sys

    _, development, holdout = native_inputs()
    paths = []
    for name, source in (("dev", development), ("hold", holdout)):
        path = tmp_path / (name + ".csv")
        path.write_text(
            "instrument_id,observed_at,open,high,low,close,volume\n"
            + "\n".join(
                f"{b.instrument_id},{b.observed_at.isoformat()},{b.open},{b.high},{b.low},{b.close},{b.volume}"
                for b in source.bars
            )
        )
        paths.append(path)
    root = tmp_path / "workspace"
    output = tmp_path / "report.json"
    arguments = [
        sys.executable,
        "-m",
        "ai_quant_os.research_cli",
        *(str(p) for p in paths),
        "--strategy",
        "moving-average-directional",
        "--candidate",
        "1:2",
        "--experiment-id",
        "directional-cli",
        "--hypothesis-id",
        "directional-cli",
        "--description",
        "Synthetic accounting test",
        "--train-bars",
        "20",
        "--validation-bars",
        "20",
        "--quantity",
        "100",
        "--fee",
        "0.10",
        "--stress-fee",
        "0.20",
        "--stress-slippage-bps",
        "10",
        "--monte-carlo",
        "--mc-iterations",
        "100",
        "--adversarial",
        "--workspace",
        str(root),
        "--output",
        str(output),
    ]
    refused = subprocess.run(arguments, capture_output=True, text=True)
    assert refused.returncode != 0 and "reversal-fee" in refused.stderr
    assert not root.exists()
    method = "CIRCULAR_EQUAL_REVERSAL_HALF_FEE_BLOCKS_SHA256_V1"
    completed = subprocess.run([*arguments, "--mc-method", method], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    record = json.loads(output.read_text())
    assert record["report"]["plan"]["monte_carlo_spec"]["method"] == method
    assert record["report"]["outcome"] == "VALIDATION_INCOMPLETE"
    assert record["report"]["monte_carlo"]["result"]["state"] == "PASS"
    original = output.read_bytes()
    repeated = subprocess.run([*arguments, "--mc-method", method], capture_output=True, text=True)
    assert repeated.returncode == 0 and output.read_bytes() == original


def test_reversal_fee_policy_refuses_inexact_analytical_allocation():
    from decimal import Inexact
    from ai_quant_os.monte_carlo import CashFill, closed_trade_series

    fills = (
        CashFill("BUY", Decimal(10), Decimal(1), Decimal(0)),
        CashFill("SELL", Decimal(20), Decimal(1), Decimal(".1234567890123456789012345679")),
    )
    with pytest.raises(Inexact):
        closed_trade_series(fills, reversal_fee_policy="EQUAL_SIZE_HALF_FEE_V1")


def test_saved_directional_statistical_report_reproduces_and_keeps_hash(tmp_path):
    pytest.importorskip("nautilus_trader")
    import json
    from hashlib import sha256
    from pydantic import TypeAdapter
    from ai_quant_os.research_results import ResearchReport
    from ai_quant_os.research_store import encode_research
    from ai_quant_os.market_data import BarDataset
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    payload = json.loads(
        (
            Path(__file__).parents[1]
            / "docs/evaluations/research-directional-statistics-baseline.json"
        ).read_text()
    )
    saved = TypeAdapter(ResearchReport).validate_python(payload["report"])
    declaration, development, _ = native_inputs()
    assert declaration == saved.plan
    # Replay the retained Parquet-derived Decimal source representation for its result hash.
    actual = run_research_experiment(
        saved.plan,
        development,
        BarDataset(saved.adversarial.source_bars),
        StrategyRegistry(tmp_path / "strategies"),
        ResearchStore(tmp_path / "research.sqlite3"),
    )
    assert saved == actual
    assert sha256(encode_research(saved)).hexdigest() == payload["report_sha256"]


def test_adversarial_rejects_forged_price_even_with_consistent_cash_ledger(tmp_path):
    pytest.importorskip("nautilus_trader")
    report, _ = run_native(tmp_path)
    scenario = report.adversarial.scenarios[0]
    result = scenario.run.result
    fill = result.fills[0]
    change = Decimal(".00001")
    cash_change = change * fill.quantity * (1 if fill.side == "SELL" else -1)
    forged = replace(
        scenario.run,
        result=replace(
            result,
            fills=(replace(fill, price=fill.price + change), *result.fills[1:]),
            net_pnl=result.net_pnl + cash_change,
        ),
    )
    with pytest.raises(ValueError, match="Adversarial"):
        replace(
            report,
            adversarial=replace(
                report.adversarial,
                scenarios=(replace(scenario, run=forged), *report.adversarial.scenarios[1:]),
            ),
            attempts=tuple(forged if row == scenario.run else row for row in report.attempts),
        )

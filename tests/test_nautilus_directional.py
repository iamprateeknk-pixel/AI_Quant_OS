from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json

import pytest

pytest.importorskip("nautilus_trader")


def bars(identifier="EURUSD"):
    from ai_quant_os.market_data import Bar, BarDataset

    return BarDataset(
        Bar(
            identifier,
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(100),
        )
        for i, price in enumerate(("1.1", "1.2", "1.3", "1.4"))
    )


class Flip:
    def __init__(self):
        self.seen = []

    def target_position(self, history):
        self.seen.append(history)
        return (1, -1, 0)[len(history) - 1]


class StreamingFlip:
    def __init__(self):
        self.closed_bars = 0
        self.decision_counts = []

    def on_bar(self, bar):
        self.closed_bars += 1

    def target_position(self):
        self.decision_counts.append(self.closed_bars)
        return (1, -1, 0)[self.closed_bars - 1]


def test_native_bar_long_short_reversal_is_causal_and_ledger_reconciled():
    from ai_quant_os.nautilus_runtime import run_nautilus_directional_backtest
    from ai_quant_os.backtest import BacktestCosts
    from ai_quant_os.native_bar_evidence import verify_native_bar_result

    source, plugin, costs = bars(), Flip(), BacktestCosts(Decimal(0), Decimal("0.1"))
    result = run_nautilus_directional_backtest(
        source, plugin, costs, strategy_ref="directional-fixture@1.0.0", quantity=10
    )
    assert plugin.seen == [source.bars[:i] for i in (1, 2, 3)]
    assert [fill.side for fill in result.fills] == ["BUY", "SELL", "BUY"]
    assert [fill.quantity for fill in result.fills] == [10, 20, 10]
    assert [fill.fee for fill in result.fills] == [Decimal("0.1")] * 3
    assert result.net_pnl == Decimal("-0.3")
    assert result.final_position == 0
    assert result.replay_model == "BAR_OPEN_CLOSE_LONG_SHORT_V1"
    assert result.pnl_method == "EXACT_FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK"
    verify_native_bar_result(result, source.bars, 10, costs)
    assert result == run_nautilus_directional_backtest(
        source, Flip(), costs, strategy_ref="directional-fixture@1.0.0", quantity=10
    )
    with pytest.raises(ValueError):
        verify_native_bar_result(
            replace(result, final_position=Decimal(10)), source.bars, 10, costs
        )


def test_directional_native_bar_replay_supports_streaming_plugins():
    from ai_quant_os.nautilus_runtime import run_nautilus_directional_backtest
    from ai_quant_os.backtest import BacktestCosts

    source, costs = bars(), BacktestCosts(Decimal(0), Decimal("0.1"))
    plugin = StreamingFlip()
    result = run_nautilus_directional_backtest(
        source, plugin, costs, strategy_ref="directional-stream-fixture@1.0.0", quantity=10
    )
    reference = run_nautilus_directional_backtest(
        source, Flip(), costs, strategy_ref="directional-stream-fixture@1.0.0", quantity=10
    )
    assert result == reference
    assert plugin.decision_counts == [1, 2, 3]
    assert plugin.closed_bars == len(source.bars)


@pytest.mark.parametrize(
    "identifier,currency",
    [("EURUSD", "USD"), ("GBPUSD", "USD"), ("USDJPY", "JPY"), ("EURJPY", "JPY")],
)
def test_open_short_marks_at_final_close_with_actual_costs(identifier, currency):
    from ai_quant_os.nautilus_runtime import run_nautilus_directional_backtest
    from ai_quant_os.backtest import BacktestCosts
    from ai_quant_os.native_bar_evidence import verify_native_bar_result

    class Short:
        def target_position(self, history):
            return -1

    source = bars(identifier)
    costs = BacktestCosts(Decimal(10), Decimal(1))
    result = run_nautilus_directional_backtest(
        source, Short(), costs, strategy_ref="short-fixture@1.0.0", quantity=10
    )
    assert result.pnl_currency == currency
    assert result.final_position == -10
    assert result.fills[0].side == "SELL"
    assert result.net_pnl == result.fills[0].price * 10 - Decimal(1) - source.bars[-1].close * 10
    verify_native_bar_result(result, source.bars, 10, costs)


def test_legacy_long_flat_rejects_short_and_directional_liquidity_is_bounded():
    from ai_quant_os.nautilus_runtime import (
        run_nautilus_directional_backtest,
        run_nautilus_backtest,
    )
    from ai_quant_os.backtest import BacktestCosts

    class Short:
        def target_position(self, history):
            return -1

    source, costs = bars(), BacktestCosts(Decimal(0), Decimal(0))
    with pytest.raises(ValueError, match="target"):
        run_nautilus_backtest(source, Short(), costs, strategy_ref="short-fixture@1.0.0")
    with pytest.raises(ValueError, match="liquidity"):
        run_nautilus_directional_backtest(
            source, Flip(), costs, strategy_ref="directional-fixture@1.0.0", quantity=500001
        )


def test_registered_directional_plugin_has_separate_immutable_source_and_cli(tmp_path, monkeypatch):
    from ai_quant_os.directional_strategy import DirectionalMovingAverage
    from ai_quant_os.strategy_contracts import StrategyGenome
    from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin
    from ai_quant_os.registered_backtest import run_registered_backtest
    from ai_quant_os.backtest import BacktestCosts
    from ai_quant_os.nautilus_cli import main

    source = bars()
    genome = StrategyGenome("moving-average-directional@1.0.0", "BAR", (("fast", 1), ("slow", 2)))
    registry = StrategyRegistry(tmp_path / "registry")
    plugin, binding = prepare_builtin(genome, registry)
    assert plugin.target_position(source.bars[:1]) == 0
    assert plugin.target_position(source.bars[:2]) == 1
    declining = tuple(
        replace(
            row,
            open=Decimal("1.4") - Decimal(i) / 10,
            high=Decimal("1.4") - Decimal(i) / 10,
            low=Decimal("1.4") - Decimal(i) / 10,
            close=Decimal("1.4") - Decimal(i) / 10,
        )
        for i, row in enumerate(source.bars)
    )
    assert plugin.target_position(declining[:2]) == -1
    with pytest.raises(ValueError):
        DirectionalMovingAverage(2, 1)
    assert "directional_strategy.py" in {row.path for row in binding.implementation.source_files}
    run = run_registered_backtest(
        source, genome, BacktestCosts(Decimal(0), Decimal(0)), registry, quantity=10
    )
    assert run.result.replay_model == "BAR_OPEN_CLOSE_LONG_SHORT_V1"
    csv = tmp_path / "bars.csv"
    csv.write_text(
        "instrument_id,observed_at,open,high,low,close,volume\n"
        + "\n".join(
            f"EURUSD,{row.observed_at.isoformat()},{row.open},{row.high},{row.low},{row.close},100"
            for row in source.bars
        )
    )
    output = tmp_path / "result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "ai-quant-backtest",
            str(csv),
            "--strategy",
            "moving-average-directional",
            "--fast-window",
            "1",
            "--slow-window",
            "2",
            "--quantity",
            "10",
            "--output",
            str(output),
        ],
    )
    main()
    assert json.loads(output.read_text())["replay_model"] == "BAR_OPEN_CLOSE_LONG_SHORT_V1"


def test_saved_directional_native_evidence_replays(tmp_path):
    from pydantic import TypeAdapter
    from ai_quant_os.registered_backtest import RegisteredBacktest
    from ai_quant_os.market_data import Bar
    from ai_quant_os.native_bar_evidence import verify_native_bar_result
    from ai_quant_os.market_data import BarDataset
    from ai_quant_os.registered_backtest import run_registered_backtest
    from ai_quant_os.strategy_registry import StrategyRegistry

    payload = json.loads(
        (
            Path(__file__).parents[1] / "docs/evaluations/nautilus-directional-baseline-0131.json"
        ).read_text()
    )
    run = TypeAdapter(RegisteredBacktest).validate_python(payload["run"])
    source = TypeAdapter(tuple[Bar, ...]).validate_python(payload["bars"])
    verify_native_bar_result(run.result, source, payload["quantity"], run.result.costs)
    assert run == run_registered_backtest(
        BarDataset(source),
        run.binding.genome,
        run.result.costs,
        StrategyRegistry(tmp_path / "strategies"),
        quantity=payload["quantity"],
    )


@pytest.mark.parametrize("target", [True, -2, 2])
def test_directional_invalid_targets_fail_native_replay(target):
    from ai_quant_os.nautilus_runtime import run_nautilus_directional_backtest
    from ai_quant_os.backtest import BacktestCosts

    class Invalid:
        def target_position(self, history):
            return target

    with pytest.raises(ValueError, match="target"):
        run_nautilus_directional_backtest(
            bars(), Invalid(), BacktestCosts(Decimal(0), Decimal(0)), strategy_ref="invalid@1.0.0"
        )


def directional_research(tmp_path):
    from test_research_pipeline import data, plan
    from ai_quant_os.market_data import BarDataset
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_contracts import StrategyGenome
    from ai_quant_os.strategy_registry import StrategyRegistry

    development = data()
    holdout = BarDataset(
        replace(
            row, open=Decimal(price), high=Decimal(price), low=Decimal(price), close=Decimal(price)
        )
        for row, price in zip(
            data(14, 8).bars, ("1.1", "1.2", "1.3", "1.4", "1.3", "1.2", "1.1", "1.0"), strict=True
        )
    )
    declaration = plan(
        development,
        holdout,
        candidates=(
            StrategyGenome("moving-average-directional@1.0.0", "BAR", (("fast", 1), ("slow", 2))),
        ),
    )
    store = ResearchStore(tmp_path / "research.sqlite3")
    strategies = StrategyRegistry(tmp_path / "strategies")
    report = run_research_experiment(declaration, development, holdout, strategies, store)
    return report, store, strategies, holdout


def test_directional_research_freezes_and_archives_real_reversals(tmp_path):
    from ai_quant_os.research_store import ResearchStore, encode_research
    from ai_quant_os.native_bar_evidence import verify_native_bar_result

    report, store, _, source = directional_research(tmp_path)
    assert report.outcome == "VALIDATION_INCOMPLETE"
    assert report.promotion_allowed is False
    assert report.selection.genome == report.holdout.binding.genome
    assert store.selection(report.plan.experiment_id) == report.selection
    assert report.holdout.result.final_position == -100
    assert [fill.quantity for fill in report.holdout.result.fills] == [100, 200]
    assert len([gate for gate in report.gates if gate.state == "PENDING"]) == 5
    verify_native_bar_result(report.holdout.result, source.bars, 100, report.plan.costs)
    restarted = ResearchStore(store.path).get(report.plan.experiment_id)
    assert encode_research(restarted) == encode_research(report)


@pytest.mark.parametrize("version", ["1.1", "1.2"])
def test_directional_unreviewed_statistical_policy_refused_before_reservation(
    tmp_path, monkeypatch, version
):
    from test_research_pipeline import data, plan
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os.adversarial import AdversarialSpec
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_contracts import StrategyGenome
    from ai_quant_os.strategy_registry import StrategyRegistry

    declaration = plan(
        schema_version=version,
        candidates=(StrategyGenome("moving-average-directional@1.0.0", "BAR"),),
        monte_carlo_spec=MonteCarloSpec(),
        adversarial_spec=AdversarialSpec() if version == "1.2" else None,
    )
    store = ResearchStore(tmp_path / "research.sqlite3")

    def forbidden(*args):
        raise AssertionError("Unsupported policy must not reserve a holdout")

    monkeypatch.setattr(store, "reserve", forbidden)
    with pytest.raises(ValueError, match="reversal-fee"):
        run_research_experiment(
            declaration, data(), data(14, 6), StrategyRegistry(tmp_path / "sources"), store
        )
    assert store.get(declaration.experiment_id) is None


def test_directional_candidate_counterfactual_and_forward_shadow_keep_model_binding(
    tmp_path, monkeypatch
):
    from ai_quant_os.research_candidates import build_candidate_bundle
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.counterfactual_contracts import CounterfactualPlan, ReplayVariant
    from ai_quant_os.counterfactual_results import ReplayInputs
    from ai_quant_os.counterfactual_pipeline import run_counterfactual
    from ai_quant_os.counterfactual_store import CounterfactualStore
    from ai_quant_os.shadow_portfolio import ShadowAssignment
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_store import ShadowStore
    from ai_quant_os.broker import SimulatedBroker
    from test_shadow_portfolio import forward_bars

    def forbidden(*args, **kwargs):
        raise AssertionError("Diagnostic replay cannot route broker orders")

    monkeypatch.setattr(SimulatedBroker, "place", forbidden)
    parent, research, strategies, source = directional_research(tmp_path)
    bundle = build_candidate_bundle(research, strategies, parent.plan.experiment_id)
    plan = CounterfactualPlan(
        "directional-comparison",
        bundle.manifest.candidate_id,
        bundle.manifest.report_sha256,
        parent.plan.holdout_fingerprint,
        (ReplayVariant("half-size", parent.selection.genome, 50, parent.plan.costs),),
    )
    replay = run_counterfactual(
        ReplayInputs(plan, bundle, source.bars),
        strategies,
        CounterfactualStore(tmp_path / "replay.sqlite3"),
    )
    assert replay.outcome == "COMPLETED"
    assert replay.results[0].run == parent.holdout
    assert [fill.quantity for fill in replay.results[1].run.result.fills] == [50, 100]
    candidates = CandidateStore(tmp_path / "candidates.sqlite3")
    view = candidates.register(bundle)
    assignment = ShadowAssignment(
        "directional-shadow", source.bars[-1].observed_at + timedelta(minutes=1), view, bundle
    )
    store = ShadowStore(tmp_path / "shadow.sqlite3")
    assign_shadow(assignment, store, candidates, strategies)
    forward = forward_bars(assignment)
    first = observe_shadow(
        assignment.shadow_id, "first", forward[:3], store, candidates, strategies
    )
    second = observe_shadow(
        assignment.shadow_id, "second", forward[3:], store, candidates, strategies
    )
    assert first.outcome == second.outcome == "OBSERVED"
    assert second.run.result.final_position == -100
    assert second.run.result.replay_model == "BAR_OPEN_CLOSE_LONG_SHORT_V1"
    assert second.run.result.fills[: len(first.run.result.fills)] == first.run.result.fills
    assert store.latest(assignment.shadow_id) == second
    assert candidates.get(bundle.manifest.candidate_id) == view
    with pytest.raises(ValueError, match="identity"):
        replace(
            second,
            run=replace(
                second.run,
                result=replace(second.run.result, replay_model="BAR_OPEN_CLOSE_QUOTES_V1"),
            ),
        )


def test_directional_equal_averages_flat_and_caller_precision_independent():
    from decimal import localcontext
    from ai_quant_os.directional_strategy import DirectionalMovingAverage

    source = bars().bars
    equal = tuple(
        replace(
            row, open=Decimal("1.2"), high=Decimal("1.2"), low=Decimal("1.2"), close=Decimal("1.2")
        )
        for row in source
    )
    plugin = DirectionalMovingAverage(1, 2)
    with localcontext() as context:
        context.prec = 2
        assert plugin.target_position(equal) == 0
        assert plugin.target_position(source) == 1


def test_saved_directional_research_reproduces_canonical_archive(tmp_path):
    from pydantic import TypeAdapter
    from ai_quant_os.research_results import ResearchReport
    from ai_quant_os.research_store import encode_research

    raw = (
        Path(__file__).parents[1] / "docs/evaluations/research-directional-baseline-0131.json"
    ).read_bytes()
    saved = TypeAdapter(ResearchReport).validate_json(raw)
    report, _, _, _ = directional_research(tmp_path)
    assert saved == report
    assert raw == encode_research(saved)

from dataclasses import replace
from decimal import Decimal, localcontext, Inexact
from hashlib import sha256
import json
from pathlib import Path

import pytest

from ai_quant_os.market_data import import_bars_csv


def test_declared_scenarios_preserve_source_and_are_context_independent():
    from ai_quant_os.adversarial import AdversarialSpec, adversarial_datasets

    source = import_bars_csv(Path(__file__).parent / "fixtures/research_mc_holdout.csv")
    fingerprint = source.fingerprint
    spec = AdversarialSpec(drop_every=5, shock_bps=1000, maximum_loss=Decimal(5))
    expected = adversarial_datasets(source, spec)
    with localcontext() as context:
        context.prec = 4
        context.traps[Inexact] = True
        actual = adversarial_datasets(source, spec)
        assert tuple(d.fingerprint for _, d in actual) == tuple(d.fingerprint for _, d in expected)
    assert source.fingerprint == fingerprint
    assert tuple(name for name, _ in expected) == ("MISSING_OBSERVATIONS", "DOWNWARD_STEP")
    assert len(expected[0][1].bars) == 320
    assert expected[1][1].bars[200].open == source.bars[200].open * Decimal("0.9")


@pytest.mark.parametrize(
    "changes",
    [
        {"drop_every": True},
        {"drop_every": 1},
        {"shock_bps": 0},
        {"maximum_loss": Decimal("NaN")},
        {"risk_currency": "EUR"},
    ],
)
def test_invalid_adversarial_policy_is_rejected(changes):
    from ai_quant_os.adversarial import AdversarialSpec

    with pytest.raises(ValueError):
        AdversarialSpec(**changes)


def test_native_adversarial_evidence_is_archived_and_cannot_skip_checks(tmp_path):
    pytest.importorskip("nautilus_trader")
    from test_research_pipeline import plan
    from ai_quant_os.adversarial import AdversarialSpec
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    root = Path(__file__).parent / "fixtures"
    dev, heldout = (
        import_bars_csv(root / f"research_mc_{role}.csv") for role in ("development", "holdout")
    )
    declared = plan(
        dev,
        heldout,
        train_bars=20,
        validation_bars=20,
        schema_version="1.2",
        monte_carlo_spec=MonteCarloSpec(iterations=100),
        adversarial_spec=AdversarialSpec(maximum_loss=Decimal(0)),
    )
    store = ResearchStore(tmp_path / "research.sqlite3")
    report = run_research_experiment(
        declared, dev, heldout, StrategyRegistry(tmp_path / "strategies"), store
    )
    assert report.adversarial is not None
    assert len(report.adversarial.scenarios) == 2
    assert all(row.run.binding == report.holdout.binding for row in report.adversarial.scenarios)
    assert all(
        row.run.result.costs == declared.stress_costs for row in report.adversarial.scenarios
    )
    assert store.get(declared.experiment_id) == report
    assert report.promotion_allowed is False
    gate = next(row for row in report.gates if row.name == "ADVERSARIAL")
    assert gate.state == report.adversarial.state
    changed = replace(report.adversarial.scenarios[0], dataset_fingerprint="0" * 64)
    with pytest.raises(ValueError, match="Adversarial"):
        replace(
            report,
            adversarial=replace(
                report.adversarial, scenarios=(changed, *report.adversarial.scenarios[1:])
            ),
        )
    with pytest.raises(ValueError):
        replace(
            report,
            gates=tuple(
                replace(row, state="PASS" if row.state != "PASS" else "FAIL")
                if row.name == "ADVERSARIAL"
                else row
                for row in report.gates
            ),
        )
    with pytest.raises(ValueError):
        replace(declared, schema_version="1.1")
    row = report.adversarial.scenarios[0]
    fabricated = replace(row.run, result=replace(row.run.result, net_pnl=Decimal("999999")))
    altered_attempts = tuple(fabricated if item == row.run else item for item in report.attempts)
    with pytest.raises(ValueError, match="Adversarial"):
        replace(
            report,
            attempts=altered_attempts,
            adversarial=replace(
                report.adversarial,
                scenarios=(replace(row, run=fabricated), *report.adversarial.scenarios[1:]),
            ),
        )


@pytest.mark.parametrize(
    "version,file",
    [("1.0", "research-native-baseline.json"), ("1.1", "research-monte-carlo-baseline.json")],
)
def test_existing_research_archives_keep_their_canonical_hash(version, file):
    from pydantic import TypeAdapter
    from ai_quant_os.research_results import ResearchReport
    from ai_quant_os.research_store import encode_research

    payload = json.loads((Path(__file__).parents[1] / "docs/evaluations" / file).read_text())
    report = TypeAdapter(ResearchReport).validate_python(payload["report"])
    assert report.schema_version == version
    assert sha256(encode_research(report)).hexdigest() == payload["report_sha256"]


def test_cli_declares_adversarial_policy_and_exports_native_runs(tmp_path):
    pytest.importorskip("nautilus_trader")
    pytest.importorskip("pyarrow")
    import subprocess
    import sys

    root = Path(__file__).parent / "fixtures"
    args = [
        sys.executable,
        "-m",
        "ai_quant_os.research_cli",
        str(root / "research_mc_development.csv"),
        str(root / "research_mc_holdout.csv"),
        "--experiment-id",
        "stress-cli",
        "--hypothesis-id",
        "stress-cli",
        "--description",
        "Synthetic scenario evidence",
        "--candidate",
        "1:2",
        "--train-bars",
        "20",
        "--validation-bars",
        "20",
        "--quantity",
        "100",
        "--fee",
        "0.10",
        "--stress-fee",
        "20",
        "--stress-slippage-bps",
        "10",
        "--workspace",
        str(tmp_path / "workspace"),
        "--output",
        str(tmp_path / "report.json"),
        "--adversarial",
        "--adversarial-loss-limit",
        "0",
    ]
    refused = subprocess.run(args, capture_output=True, text=True)
    assert refused.returncode != 0
    assert "requires --monte-carlo" in refused.stderr
    assert not (tmp_path / "workspace/research.sqlite3").exists()
    completed = subprocess.run(
        [*args, "--monte-carlo", "--mc-iterations", "100"], capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads((tmp_path / "report.json").read_text())
    assert payload["schema_version"] == "1.2"
    evidence = payload["report"]["adversarial"]
    assert len(evidence["scenarios"]) == 2
    assert evidence["state"] == "FAIL"
    assert payload["report"]["outcome"] == "REJECTED"


def test_inactive_scenarios_cannot_pass_or_promote(tmp_path):
    pytest.importorskip("nautilus_trader")
    from test_research_pipeline import plan
    from ai_quant_os.adversarial import AdversarialSpec
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os.strategy_contracts import StrategyGenome
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    root = Path(__file__).parent / "fixtures"
    dev, heldout = (
        import_bars_csv(root / f"research_mc_{role}.csv") for role in ("development", "holdout")
    )
    declared = plan(
        dev,
        heldout,
        train_bars=20,
        validation_bars=20,
        schema_version="1.2",
        candidates=(
            StrategyGenome("moving-average-cross@1.0.0", "BAR", (("fast", 1), ("slow", 4096))),
        ),
        monte_carlo_spec=MonteCarloSpec(iterations=100),
        adversarial_spec=AdversarialSpec(),
    )
    report = run_research_experiment(
        declared,
        dev,
        heldout,
        StrategyRegistry(tmp_path / "strategies"),
        ResearchStore(tmp_path / "research.sqlite3"),
    )
    assert report.adversarial.state == "PENDING"
    assert report.adversarial.reason == "SCENARIO_ACTIVITY_MISSING"
    assert report.promotion_allowed is False


def test_scenario_execution_failure_retains_completed_attempts(tmp_path, monkeypatch):
    pytest.importorskip("nautilus_trader")
    from test_research_pipeline import plan
    from ai_quant_os.adversarial import AdversarialSpec
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os import research_pipeline
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    root = Path(__file__).parent / "fixtures"
    dev, heldout = (
        import_bars_csv(root / f"research_mc_{role}.csv") for role in ("development", "holdout")
    )
    declared = plan(
        dev,
        heldout,
        train_bars=20,
        validation_bars=20,
        schema_version="1.2",
        monte_carlo_spec=MonteCarloSpec(iterations=100),
        adversarial_spec=AdversarialSpec(),
    )
    original, calls = research_pipeline.run_registered_backtest, []

    def failing(*args, **kwargs):
        calls.append(None)
        if len(calls) == 14:
            raise ValueError("Private scenario details")
        return original(*args, **kwargs)

    monkeypatch.setattr(research_pipeline, "run_registered_backtest", failing)
    store = ResearchStore(tmp_path / "research.sqlite3")
    report = research_pipeline.run_research_experiment(
        declared, dev, heldout, StrategyRegistry(tmp_path / "strategies"), store
    )
    assert report.outcome == "FAILED"
    assert report.reasons == ("ADVERSARIAL_EXECUTION_FAILURE",)
    assert len(report.attempts) == 13
    assert report.adversarial is None
    assert store.get(declared.experiment_id) == report

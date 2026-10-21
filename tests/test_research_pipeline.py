from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.market_data import Bar, BarDataset
from ai_quant_os.strategy_contracts import StrategyGenome


def test_archive_accepts_one_immutable_input_snapshot(tmp_path):
    pytest.importorskip("pyarrow")
    from pathlib import Path
    from ai_quant_os.data_archive import BarArchive

    source = Path(__file__).parent / "fixtures" / "research_development.csv"
    archive = BarArchive(tmp_path)
    raw = source.read_bytes()
    manifest = archive.ingest_csv_bytes(
        raw, source_id="snapshot", ingested_at=datetime.now(timezone.utc)
    )
    assert archive.replay(manifest.archive_id).fingerprint == data().fingerprint
    assert manifest == archive.ingest_csv(
        source, source_id="snapshot", ingested_at=datetime.now(timezone.utc)
    )


@pytest.mark.parametrize("strategy", ["moving-average-cross", "moving-average-directional"])
def test_research_cli_uses_verified_archives_and_retrieves_same_report(tmp_path, strategy):
    pytest.importorskip("nautilus_trader")
    pytest.importorskip("pyarrow")
    import subprocess
    import sys
    from ai_quant_os.research_store import ResearchStore

    paths = []
    for name, dataset in (("development", data()), ("holdout", data(14, 6))):
        path = tmp_path / (name + ".csv")
        path.write_text(
            "instrument_id,observed_at,open,high,low,close,volume\n"
            + "\n".join(
                f"{b.instrument_id},{b.observed_at.isoformat()},{b.open},{b.high},{b.low},{b.close},{b.volume}"
                for b in dataset.bars
            ),
            encoding="utf-8",
        )
        paths.append(path)
    output, workspace = tmp_path / "result.json", tmp_path / "research"
    args = [
        sys.executable,
        "-m",
        "ai_quant_os.research_cli",
        *(str(p) for p in paths),
        "--experiment-id",
        "cli-fixture",
        "--hypothesis-id",
        "cli-hypothesis",
        "--description",
        "Synthetic interface evaluation",
        "--candidate",
        "1:2",
        "--candidate",
        "2:3",
        "--strategy",
        strategy,
        "--train-bars",
        "6",
        "--validation-bars",
        "4",
        "--quantity",
        "100",
        "--fee",
        "0.10",
        "--stress-fee",
        "0.20",
        "--stress-slippage-bps",
        "10",
        "--workspace",
        str(workspace),
        "--output",
        str(output),
    ]
    proc = subprocess.run(args, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    record = json.loads(output.read_text())
    assert record["report"]["outcome"] == "VALIDATION_INCOMPLETE"
    assert record["report"]["promotion_allowed"] is False
    assert record["report"]["holdout"]["binding"]["genome"]["strategy_ref"] == strategy + "@1.0.0"
    assert len(record["source_lineage"]) == 2
    assert len(tuple((workspace / "archives" / "manifests").glob("*.json"))) == 2
    assert ResearchStore(workspace / "research.sqlite3").get("cli-fixture").selection is not None
    before = output.read_bytes()
    repeated = subprocess.run(args, capture_output=True, text=True)
    assert repeated.returncode == 0, repeated.stderr
    assert output.read_bytes() == before


def data(start=0, count=14):
    return BarDataset(
        Bar(
            "EURUSD",
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            *(Decimal("1.00") + Decimal(i) / 100 for _ in range(4)),
            Decimal(100),
        )
        for i in range(start, start + count)
    )


def plan(development=None, holdout=None, **changes):
    from ai_quant_os.research_contracts import Hypothesis, ResearchPlan, ResearchDatasetScope

    development, holdout = development or data(), holdout or data(14, 6)
    values = dict(
        experiment_id="fixture",
        hypothesis=Hypothesis("ma-fixture", "Synthetic moving-average interface evaluation"),
        candidates=(
            StrategyGenome("moving-average-cross@1.0.0", "BAR", (("fast", 1), ("slow", 2))),
            StrategyGenome("moving-average-cross@1.0.0", "BAR", (("fast", 2), ("slow", 3))),
        ),
        development_fingerprint=development.fingerprint,
        development_scope=ResearchDatasetScope(
            development.bars[0].instrument_id,
            development.bars[0].observed_at,
            development.bars[-1].observed_at,
            development.fingerprint,
        ),
        holdout_fingerprint=holdout.fingerprint,
        holdout_scope=ResearchDatasetScope(
            holdout.bars[0].instrument_id,
            holdout.bars[0].observed_at,
            holdout.bars[-1].observed_at,
            holdout.fingerprint,
        ),
        train_bars=6,
        validation_bars=4,
        quantity=100,
        costs=BacktestCosts(Decimal(0), Decimal("0.10")),
        stress_costs=BacktestCosts(Decimal(10), Decimal("0.20")),
    )
    values.update(changes)
    return ResearchPlan(**values)


def test_expanding_walk_forward_splits_never_train_on_current_validation():
    from ai_quant_os.research_contracts import partition_development

    folds = partition_development(data(), train_bars=6, validation_bars=4)
    assert [(len(a.bars), len(b.bars)) for a, b in folds] == [(6, 4), (10, 4)]
    assert all(a.bars[-1].observed_at < b.bars[0].observed_at for a, b in folds)
    assert {bar.observed_at for _, b in folds for bar in b.bars} == {
        bar.observed_at for bar in data().bars[6:]
    }
    with pytest.raises(ValueError):
        partition_development(data(count=15), train_bars=6, validation_bars=4)


def test_selection_diagnostic_measures_symmetric_cross_validation_overfit_probability():
    from decimal import Context, Inexact, localcontext
    from types import SimpleNamespace

    from ai_quant_os.research_results import selection_diagnostic

    candidate_ids = ("a" * 64, "b" * 64)
    pnl_by_fold = ((10, 9), (10, 9), (-10, 0), (-10, 0))
    folds = []
    for pnl_pair in pnl_by_fold:
        rows = tuple(
            SimpleNamespace(
                binding=SimpleNamespace(genome_sha256=candidate_id),
                result=SimpleNamespace(net_pnl=Decimal(pnl)),
            )
            for candidate_id, pnl in zip(candidate_ids, pnl_pair, strict=True)
        )
        folds.append(SimpleNamespace(validation_candidates=rows, validation=rows[0]))

    with localcontext(Context(prec=2, traps=[Inexact])):
        diagnostic = selection_diagnostic(tuple(folds))

    assert diagnostic.method == "CSCV_EQUAL_BLOCK_NET_PNL_RANK_V1"
    assert diagnostic.cscv_split_count == 3
    assert diagnostic.cscv_below_median_count == 1
    assert diagnostic.cscv_probability == Decimal(1) / Decimal(3)


def test_v13_report_schema_accepts_native_decimal_exponent_strings():
    from pathlib import Path

    from jsonschema import validate

    schema = json.loads(
        (Path(__file__).parents[1] / "docs/schemas/research-result-v1.3.schema.json").read_text()
    )

    validate("0E-18", schema["$defs"]["NautilusResult"]["properties"]["net_pnl"])


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": True},
        {"train_bars": 1},
        {"candidates": ()},
        {"stress_costs": BacktestCosts(Decimal(0), Decimal("0.10"))},
        {"experiment_id": "../escape"},
    ],
)
def test_plan_rejects_invalid_or_missing_stress_contract(change):
    with pytest.raises(ValueError):
        plan(**change)


def test_native_pipeline_freezes_selection_before_holdout_and_caches_report(tmp_path, monkeypatch):
    pytest.importorskip("nautilus_trader")
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry
    import ai_quant_os.research_pipeline as pipeline

    store = ResearchStore(tmp_path / "research.sqlite3")
    registry = StrategyRegistry(tmp_path / "strategies")
    original = pipeline.run_registered_backtest
    seen = []

    def observed(dataset, *args, **kwargs):
        seen.append(dataset.bars[0].observed_at)
        if dataset.fingerprint == data(14, 6).fingerprint:
            assert store.selection("fixture") is not None
        return original(dataset, *args, **kwargs)

    monkeypatch.setattr(pipeline, "run_registered_backtest", observed)
    report = pipeline.run_research_experiment(plan(), data(), data(14, 6), registry, store)
    assert report.outcome == "VALIDATION_INCOMPLETE"
    assert report.promotion_allowed is False
    from dataclasses import replace

    with pytest.raises(ValueError, match="gates"):
        replace(report, gates=report.gates[:4])
    with pytest.raises(ValueError, match="promotion"):
        replace(report, promotion_allowed=True)
    with pytest.raises(ValueError, match="Measured"):
        replace(report, gates=(replace(report.gates[0], state="PENDING"), *report.gates[1:]))
    assert len(report.folds) == 2
    assert report.holdout.result.dataset_fingerprint == data(14, 6).fingerprint
    assert report.holdout.binding.genome == report.selection.genome
    assert {gate.name for gate in report.gates if gate.state == "PENDING"} >= {
        "MONTE_CARLO",
        "ADVERSARIAL",
        "INDEPENDENT_REVIEW",
        "SHADOW",
    }
    before = len(seen)
    restarted = ResearchStore(tmp_path / "research.sqlite3")
    assert (
        pipeline.run_research_experiment(plan(), data(), data(14, 6), registry, restarted) == report
    )
    assert len(seen) == before


def test_overlapping_or_changed_holdout_is_rejected_before_any_run(tmp_path):
    pytest.importorskip("nautilus_trader")
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    store = ResearchStore(tmp_path / "research.sqlite3")
    registry = StrategyRegistry(tmp_path / "strategies")
    overlapping = data(12, 6)
    with pytest.raises(ValueError, match="separat"):
        run_research_experiment(plan(holdout=overlapping), data(), overlapping, registry, store)
    with pytest.raises(ValueError, match="fingerprint"):
        run_research_experiment(plan(), data(), data(15, 6), registry, store)
    assert store.get("fixture") is None


def test_failed_candidate_and_negative_validation_are_archived(tmp_path):
    pytest.importorskip("nautilus_trader")
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    store = ResearchStore(tmp_path / "research.sqlite3")
    registry = StrategyRegistry(tmp_path / "strategies")
    bad = plan(candidates=(StrategyGenome("unknown@1.0.0", "BAR"),))
    report = run_research_experiment(bad, data(), data(14, 6), registry, store)
    assert report.outcome == "FAILED" and "INVALID_CANDIDATE" in report.reasons
    assert store.get("fixture") == report
    rejected = run_research_experiment(
        plan(holdout=data(20, 6), experiment_id="cost-failure", quantity=1),
        data(),
        data(20, 6),
        registry,
        store,
    )
    assert rejected.outcome == "REJECTED"
    assert any(gate.state == "FAIL" for gate in rejected.gates)
    assert store.get("cost-failure") == rejected
    from dataclasses import replace

    with pytest.raises(ValueError, match="Measured"):
        replace(
            rejected,
            outcome="VALIDATION_INCOMPLETE",
            gates=tuple(
                replace(gate, state="PASS") if gate.state == "FAIL" else gate
                for gate in rejected.gates
            ),
        )


def test_reserved_crashed_run_is_not_blindly_executed_and_tampering_fails(tmp_path):
    pytest.importorskip("nautilus_trader")
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    store = ResearchStore(tmp_path / "research.sqlite3")
    store.reserve(plan())
    with pytest.raises(ValueError, match="Uncertain"):
        run_research_experiment(
            plan(), data(), data(14, 6), StrategyRegistry(tmp_path / "strategies"), store
        )


def test_terminal_holdout_cannot_be_reused_or_relabelled_for_new_selection(tmp_path):
    pytest.importorskip("nautilus_trader")
    from dataclasses import replace
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    store = ResearchStore(tmp_path / "research.sqlite3")
    registry = StrategyRegistry(tmp_path / "strategies")
    first = run_research_experiment(plan(), data(), data(14, 6), registry, store)
    assert first.selection is not None
    changed = BarDataset(
        replace(bar, close=bar.close + Decimal("0.00001"), high=bar.high + Decimal("0.00001"))
        for bar in data(14, 6).bars
    )
    with pytest.raises(ValueError, match="Holdout"):
        run_research_experiment(
            plan(holdout=changed, experiment_id="relabelled"), data(), changed, registry, store
        )
    with pytest.raises(ValueError, match="Development"):
        run_research_experiment(
            plan(
                development=data(count=18),
                holdout=data(20, 6),
                experiment_id="training-leak",
                train_bars=6,
            ),
            data(count=18),
            data(20, 6),
            registry,
            store,
        )


def test_native_failure_keeps_completed_attempts_and_tamper_detection(tmp_path):
    pytest.importorskip("nautilus_trader")
    import sqlite3
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    store = ResearchStore(tmp_path / "research.sqlite3")
    report = run_research_experiment(
        plan(stress_costs=BacktestCosts(Decimal(10000), Decimal("0.20"))),
        data(),
        data(14, 6),
        StrategyRegistry(tmp_path / "strategies"),
        store,
    )
    assert report.outcome == "FAILED" and report.attempts
    assert store.get("fixture") == report
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE research_experiments SET report=? WHERE experiment_id=?", (b"{}", "fixture")
        )
    with pytest.raises(ValueError, match="fingerprint"):
        store.get("fixture")


def test_concurrent_reservation_assigns_holdout_only_once(tmp_path):
    pytest.importorskip("nautilus_trader")
    from concurrent.futures import ThreadPoolExecutor
    from ai_quant_os.research_store import ResearchStore

    store = ResearchStore(tmp_path / "research.sqlite3")

    def reserve(index):
        try:
            return store.reserve(plan(experiment_id=f"experiment-{index}")).token
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = tuple(pool.map(reserve, range(4)))
    assert sum(token is not None for token in claimed) == 1


def test_declared_monte_carlo_is_bound_to_closed_native_trades_and_archives(tmp_path):
    pytest.importorskip("nautilus_trader")
    from dataclasses import replace
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    def cyclic(start, count):
        rows = []
        for i in range(start, start + count):
            step = i % 20
            price = Decimal(1) + Decimal(step if step <= 10 else 20 - step) / 100
            rows.append(
                Bar(
                    "EURUSD",
                    datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
                    price,
                    price,
                    price,
                    price,
                    Decimal(100),
                )
            )
        return BarDataset(rows)

    development, holdout = cyclic(0, 60), cyclic(60, 400)
    spec = MonteCarloSpec(iterations=100)
    declared = plan(
        development,
        holdout,
        train_bars=20,
        validation_bars=20,
        schema_version="1.1",
        monte_carlo_spec=spec,
    )
    store = ResearchStore(tmp_path / "research.sqlite3")
    report = run_research_experiment(
        declared, development, holdout, StrategyRegistry(tmp_path / "strategies"), store
    )
    assert report.schema_version == "1.1" and report.monte_carlo.result.state == "PASS"
    assert report.monte_carlo.result.closed_trade_count == 20
    assert next(g for g in report.gates if g.name == "MONTE_CARLO").state == "PASS"
    assert report.promotion_allowed is False and report.outcome == "VALIDATION_INCOMPLETE"
    assert store.get("fixture") == report
    with pytest.raises(ValueError, match="Monte Carlo"):
        replace(
            report,
            monte_carlo=replace(
                report.monte_carlo,
                result=replace(report.monte_carlo.result, drawdown_p95=Decimal(999)),
            ),
        )
    with pytest.raises(ValueError, match="Monte Carlo"):
        replace(
            report,
            outcome="FAILED",
            reasons=("TEST",),
            schema_version="1.0",
            plan=replace(declared, schema_version="1.0", monte_carlo_spec=None),
        )


def test_v13_retains_all_validation_trials_for_selection_overfit_diagnostic(tmp_path):
    pytest.importorskip("nautilus_trader")
    from hashlib import sha256
    from pathlib import Path

    from jsonschema import ValidationError, validate

    from ai_quant_os.adversarial import AdversarialSpec
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore, encode_research
    from ai_quant_os.strategy_registry import StrategyRegistry

    development, holdout = data(count=22), data(22, 20)
    declared = plan(
        development,
        holdout,
        schema_version="1.3",
        monte_carlo_spec=MonteCarloSpec(iterations=100),
        adversarial_spec=AdversarialSpec(),
    )
    report = run_research_experiment(
        declared,
        development,
        holdout,
        StrategyRegistry(tmp_path / "strategies"),
        ResearchStore(tmp_path / "research.sqlite3"),
    )

    assert report.schema_version == "1.3"
    assert all(len(fold.validation_candidates) == 2 for fold in report.folds)
    assert all(
        {row.binding.genome_sha256 for row in fold.validation_candidates}
        == {row.fingerprint for row in declared.candidates}
        for fold in report.folds
    )
    assert all(
        {row.result.dataset_fingerprint for row in fold.validation_candidates}
        == {fold.validation.result.dataset_fingerprint}
        for fold in report.folds
    )
    assert report.selection_diagnostic.candidate_count == 2
    assert len(report.selection_diagnostic.selected_winner_ranks) == len(report.folds)
    assert report.selection_diagnostic.cscv_split_count == 3
    assert report.promotion_allowed is False
    assert ResearchStore(tmp_path / "research.sqlite3").get("fixture") == report
    artifact = {
        "schema_version": "1.3",
        "report": json.loads(encode_research(report)),
        "report_sha256": sha256(encode_research(report)).hexdigest(),
        "source_lineage": [
            {
                "role": role,
                "archive_id": "a" * 64,
                "source_sha256": "b" * 64,
                "dataset_fingerprint": fingerprint,
            }
            for role, fingerprint in (
                ("DEVELOPMENT", development.fingerprint),
                ("HOLDOUT", holdout.fingerprint),
            )
        ],
    }
    schema = json.loads(
        (Path(__file__).parents[1] / "docs/schemas/research-result-v1.3.schema.json").read_text()
    )
    validate(artifact, schema)
    invalid_artifacts = []
    without_diagnostic = json.loads(json.dumps(artifact))
    del without_diagnostic["report"]["selection_diagnostic"]
    invalid_artifacts.append(without_diagnostic)
    without_validation_trials = json.loads(json.dumps(artifact))
    del without_validation_trials["report"]["folds"][0]["validation_candidates"]
    invalid_artifacts.append(without_validation_trials)
    mismatched_plan = json.loads(json.dumps(artifact))
    mismatched_plan["report"]["plan"]["schema_version"] = "1.2"
    invalid_artifacts.append(mismatched_plan)
    unbound_diagnostic = json.loads(json.dumps(artifact))
    del unbound_diagnostic["report"]["selection_diagnostic"]["cscv_probability"]
    invalid_artifacts.append(unbound_diagnostic)
    for invalid_artifact in invalid_artifacts:
        with pytest.raises(ValidationError):
            validate(invalid_artifact, schema)


def test_v13_rejects_over_budget_candidate_trial_matrix_before_reservation(tmp_path):
    pytest.importorskip("nautilus_trader")
    from ai_quant_os.adversarial import AdversarialSpec
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    development, holdout = data(count=34), data(34, 20)
    candidates = tuple(
        StrategyGenome(
            "moving-average-cross@1.0.0",
            "BAR",
            (("fast", index + 1), ("slow", index + 2)),
        )
        for index in range(16)
    )
    declared = plan(
        development,
        holdout,
        candidates=candidates,
        train_bars=2,
        validation_bars=2,
        schema_version="1.3",
        monte_carlo_spec=MonteCarloSpec(iterations=100),
        adversarial_spec=AdversarialSpec(),
    )
    store = ResearchStore(tmp_path / "research.sqlite3")

    with pytest.raises(ValueError, match="execution budget"):
        run_research_experiment(
            declared,
            development,
            holdout,
            StrategyRegistry(tmp_path / "strategies"),
            store,
        )

    assert store.get("fixture") is None


@pytest.mark.parametrize("development_count", [14, 26])
def test_v13_rejects_too_few_or_odd_validation_folds_before_reservation(
    tmp_path, development_count
):
    pytest.importorskip("nautilus_trader")
    from ai_quant_os.adversarial import AdversarialSpec
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    development, holdout = data(count=development_count), data(development_count, 20)
    declared = plan(
        development,
        holdout,
        schema_version="1.3",
        monte_carlo_spec=MonteCarloSpec(iterations=100),
        adversarial_spec=AdversarialSpec(),
    )
    store = ResearchStore(tmp_path / f"research-{development_count}.sqlite3")

    with pytest.raises(ValueError, match="even set of at least four"):
        run_research_experiment(
            declared,
            development,
            holdout,
            StrategyRegistry(tmp_path / f"strategies-{development_count}"),
            store,
        )

    assert store.get("fixture") is None


def test_preexisting_version_one_report_remains_byte_identical():
    pytest.importorskip("nautilus_trader")
    from pathlib import Path
    from hashlib import sha256
    from pydantic import TypeAdapter
    from ai_quant_os.research_results import ResearchReport
    from ai_quant_os.research_store import encode_research

    artifact = json.loads(
        (Path(__file__).parents[1] / "docs/evaluations/research-native-baseline.json").read_text()
    )
    report = TypeAdapter(ResearchReport).validate_python(artifact["report"])
    assert sha256(encode_research(report)).hexdigest() == artifact["report_sha256"]


def test_cli_declares_monte_carlo_before_execution(tmp_path):
    pytest.importorskip("nautilus_trader")
    pytest.importorskip("pyarrow")
    import subprocess
    import sys
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures"
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        "-m",
        "ai_quant_os.research_cli",
        str(fixtures / "research_development.csv"),
        str(fixtures / "research_holdout.csv"),
        "--experiment-id",
        "mc-cli",
        "--hypothesis-id",
        "mc-cli-hypothesis",
        "--description",
        "Synthetic policy declaration",
        "--candidate",
        "1:2",
        "--train-bars",
        "6",
        "--validation-bars",
        "4",
        "--quantity",
        "100",
        "--fee",
        "0.10",
        "--stress-fee",
        "0.20",
        "--stress-slippage-bps",
        "10",
        "--monte-carlo",
        "--mc-seed",
        "123",
        "--mc-iterations",
        "100",
        "--workspace",
        str(tmp_path / "workspace"),
        "--output",
        str(output),
    ]
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    artifact = json.loads(output.read_text())
    assert artifact["schema_version"] == "1.1"
    assert artifact["report"]["plan"]["monte_carlo_spec"]["seed"] == 123
    assert artifact["report"]["monte_carlo"]["result"]["state"] == "PENDING"
    assert artifact["report"]["monte_carlo"]["result"]["reason"] == "UNCLOSED_POSITION"

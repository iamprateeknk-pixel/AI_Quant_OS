from dataclasses import replace
from decimal import Decimal
import json
import sqlite3

import pytest

pytest.importorskip("nautilus_trader")


def setup_replay(tmp_path):
    from test_research_candidates import archived_workspace
    from ai_quant_os.research_candidates import build_candidate_bundle
    from ai_quant_os.counterfactual_contracts import CounterfactualPlan, ReplayVariant
    from ai_quant_os.counterfactual_results import ReplayInputs
    from ai_quant_os.backtest import BacktestCosts

    report, research, strategies = archived_workspace(
        tmp_path, fixture_name="research-adversarial-baseline-0131.json"
    )
    candidate = build_candidate_bundle(research, strategies, report.plan.experiment_id)
    genome = candidate.manifest.selected_genome
    plan = CounterfactualPlan(
        "replay-fixture",
        candidate.manifest.candidate_id,
        candidate.manifest.report_sha256,
        report.plan.holdout_fingerprint,
        (
            ReplayVariant("half-size", genome, 50, report.plan.costs),
            ReplayVariant("high-cost", genome, 100, BacktestCosts(Decimal(10), Decimal(20))),
        ),
    )
    return ReplayInputs(plan, candidate, report.adversarial.source_bars), strategies


def test_counterfactual_reproduces_parent_and_isolates_native_alternatives(tmp_path, monkeypatch):
    from ai_quant_os.counterfactual_pipeline import run_counterfactual
    from ai_quant_os.counterfactual_store import CounterfactualStore
    from ai_quant_os.broker import SimulatedBroker

    inputs, strategies = setup_replay(tmp_path)
    original_candidate = inputs.candidate

    def forbidden(*args, **kwargs):
        raise AssertionError("Counterfactual cannot route through the paper broker")

    monkeypatch.setattr(SimulatedBroker, "place", forbidden)
    store = CounterfactualStore(tmp_path / "counterfactual.sqlite3")
    report = run_counterfactual(inputs, strategies, store)
    assert report.outcome == "COMPLETED"
    assert report.promotion_allowed is False
    assert report.results[0].run == original_candidate.report.holdout
    assert [row.variant.name for row in report.results] == ["BASELINE", "half-size", "high-cost"]
    assert report.results[1].run.result.net_pnl < report.results[0].run.result.net_pnl
    assert report.results[2].run.result.net_pnl < 0
    assert inputs.candidate == original_candidate
    assert store.get(inputs.plan.replay_id) == report

    def never_rerun(*args, **kwargs):
        raise AssertionError("Cached report must not execute")

    monkeypatch.setattr("ai_quant_os.counterfactual_pipeline.run_registered_backtest", never_rerun)
    assert run_counterfactual(inputs, strategies, CounterfactualStore(store.path)) == report
    with pytest.raises(ValueError):
        replace(report, promotion_allowed=True)
    with pytest.raises(ValueError):
        replace(report, results=report.results[:-1])
    with pytest.raises(ValueError):
        altered = replace(report.results[1], pnl_difference=Decimal("999"))
        replace(report, results=(report.results[0], altered, report.results[2]))


def test_counterfactual_source_and_identity_cannot_be_rebound(tmp_path):
    from ai_quant_os.counterfactual_results import ReplayInputs

    inputs, _ = setup_replay(tmp_path)
    with pytest.raises(ValueError):
        replace(inputs.plan, variants=(replace(inputs.plan.variants[0], quantity=True),))
    with pytest.raises(ValueError):
        replace(inputs, source_bars=inputs.source_bars[:-1])
    with pytest.raises(ValueError):
        replace(inputs, plan=replace(inputs.plan, candidate_report_sha256="0" * 64))
    with pytest.raises(ValueError):
        replace(
            inputs,
            plan=replace(inputs.plan, variants=(replace(inputs.plan.variants[0], quantity=100),)),
        )
    assert ReplayInputs(inputs.plan, inputs.candidate, inputs.source_bars) == inputs


def test_failed_native_variant_archives_completed_results(tmp_path, monkeypatch):
    from ai_quant_os import counterfactual_pipeline
    from ai_quant_os.counterfactual_store import CounterfactualStore

    inputs, strategies = setup_replay(tmp_path)
    original, calls = counterfactual_pipeline.run_registered_backtest, []

    def failing(*args, **kwargs):
        calls.append(None)
        if len(calls) == 3:
            raise ValueError("Private engine diagnostics")
        return original(*args, **kwargs)

    monkeypatch.setattr(counterfactual_pipeline, "run_registered_backtest", failing)
    store = CounterfactualStore(tmp_path / "counterfactual.sqlite3")
    report = counterfactual_pipeline.run_counterfactual(inputs, strategies, store)
    assert report.outcome == "FAILED"
    assert report.reason == "NATIVE_EXECUTION_FAILURE"
    assert len(report.results) == 2
    assert store.get(inputs.plan.replay_id) == report


def test_uncertain_reservation_and_changed_request_are_never_reexecuted(tmp_path):
    from ai_quant_os.counterfactual_store import CounterfactualStore
    from ai_quant_os.counterfactual_pipeline import run_counterfactual

    inputs, strategies = setup_replay(tmp_path)
    store = CounterfactualStore(tmp_path / "counterfactual.sqlite3")
    store.reserve(inputs)
    with pytest.raises(ValueError, match="Uncertain"):
        run_counterfactual(inputs, strategies, store)
    changed = replace(
        inputs, plan=replace(inputs.plan, variants=(replace(inputs.plan.variants[0], quantity=25),))
    )
    with pytest.raises(ValueError, match="Immutable"):
        store.reserve(changed)


def test_counterfactual_archive_corruption_is_detected(tmp_path):
    from ai_quant_os.counterfactual_store import CounterfactualStore
    from ai_quant_os.counterfactual_pipeline import run_counterfactual

    inputs, strategies = setup_replay(tmp_path)
    store = CounterfactualStore(tmp_path / "counterfactual.sqlite3")
    run_counterfactual(inputs, strategies, store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE counterfactual_replays SET report=?", (b"{}",))
    with pytest.raises(ValueError, match="fingerprint"):
        store.get(inputs.plan.replay_id)


def test_baseline_difference_stops_alternatives_and_retains_evidence(tmp_path, monkeypatch):
    from ai_quant_os import counterfactual_pipeline
    from ai_quant_os.counterfactual_store import CounterfactualStore

    inputs, strategies = setup_replay(tmp_path)
    original = counterfactual_pipeline.run_registered_backtest
    calls = []

    def changed(*args, **kwargs):
        calls.append(None)
        run = original(*args, **kwargs)
        return replace(run, result=replace(run.result, native_realized_pnl=Decimal("999")))

    monkeypatch.setattr(counterfactual_pipeline, "run_registered_backtest", changed)
    store = CounterfactualStore(tmp_path / "counterfactual.sqlite3")
    report = counterfactual_pipeline.run_counterfactual(inputs, strategies, store)
    assert len(calls) == 1
    assert report.reason == "BASELINE_REPRODUCIBILITY_FAILURE"
    assert len(report.results) == 1
    assert store.get(inputs.plan.replay_id) == report


def test_source_failure_and_reservation_ownership(tmp_path, monkeypatch):
    from ai_quant_os import counterfactual_pipeline
    from ai_quant_os.counterfactual_store import CounterfactualStore

    inputs, strategies = setup_replay(tmp_path)

    def invalid(*args, **kwargs):
        raise ValueError("Modified implementation")

    monkeypatch.setattr(counterfactual_pipeline, "prepare_builtin", invalid)
    store = CounterfactualStore(tmp_path / "counterfactual.sqlite3")
    report = counterfactual_pipeline.run_counterfactual(inputs, strategies, store)
    assert report.outcome == "FAILED"
    assert report.reason == "SOURCE_VERIFICATION_FAILURE"
    assert not report.results
    other = CounterfactualStore(tmp_path / "other.sqlite3")
    token, _ = other.reserve(inputs)
    with pytest.raises(ValueError, match="ownership"):
        other.finish("0" * 32, report)
    other.finish(token, report)
    with pytest.raises(ValueError, match="ownership"):
        other.finish(token, report)


def test_native_ledger_tampering_and_decimal_context_are_rejected(tmp_path):
    from decimal import localcontext
    from ai_quant_os.counterfactual_pipeline import run_counterfactual
    from ai_quant_os.counterfactual_store import CounterfactualStore

    inputs, strategies = setup_replay(tmp_path)
    with localcontext() as context:
        context.prec = 3
        report = run_counterfactual(
            inputs, strategies, CounterfactualStore(tmp_path / "replay.sqlite3")
        )
    row = report.results[1]
    for altered in (
        replace(row.run.result, net_pnl=Decimal("999")),
        replace(row.run.result, final_position=Decimal("999")),
        replace(row.run.result, fills=row.run.result.fills[:-1]),
    ):
        with pytest.raises(ValueError):
            replace(
                report,
                results=(
                    report.results[0],
                    replace(row, run=replace(row.run, result=altered)),
                    report.results[2],
                ),
            )


def test_cli_replays_registered_candidate_and_protects_storage(tmp_path, monkeypatch, capsys):
    from ai_quant_os.counterfactual_cli import main
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_store import encode_research

    inputs, _ = setup_replay(tmp_path)
    store = CandidateStore(tmp_path / "candidates.sqlite3")
    store.register(inputs.candidate)
    declarations = tmp_path / "variants.json"
    declarations.write_text(json.dumps(json.loads(encode_research(inputs.plan))["variants"]))
    output = tmp_path / "replay.json"
    args = [
        "ai-quant-counterfactual",
        "--workspace",
        str(tmp_path),
        "--candidate-id",
        inputs.plan.candidate_id,
        "--replay-id",
        "cli-replay",
        "--variants",
        str(declarations),
        "--output",
        str(output),
    ]
    monkeypatch.setattr("sys.argv", args)
    main()
    saved = output.read_bytes()
    assert json.loads(saved)["outcome"] == "COMPLETED"
    main()
    assert output.read_bytes() == saved
    assert "already-seen-data" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", args[:-1] + [str(tmp_path / "candidates.sqlite3")])
    with pytest.raises(SystemExit):
        main()
    assert store.bundle(inputs.plan.candidate_id) == inputs.candidate


def test_declarations_reject_unresolved_parameters_and_duplicate_variants(tmp_path):
    inputs, _ = setup_replay(tmp_path)
    variant = inputs.plan.variants[0]
    with pytest.raises(ValueError):
        replace(inputs.plan, variants=(variant, replace(variant, name="duplicate")))
    with pytest.raises(ValueError):
        replace(
            inputs,
            plan=replace(
                inputs.plan,
                variants=(replace(variant, genome=replace(variant.genome, parameters=())),),
            ),
        )
    with pytest.raises(ValueError):
        replace(inputs.plan, variants=(replace(variant, name="baseline"),))


def test_competing_reservations_have_one_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from ai_quant_os.counterfactual_store import CounterfactualStore

    inputs, _ = setup_replay(tmp_path)
    store = CounterfactualStore(tmp_path / "counterfactual.sqlite3")

    def reserve():
        try:
            return store.reserve(inputs)[0]
        except ValueError as error:
            assert "Uncertain" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(token is not None for token in tokens) == 1


def test_saved_counterfactual_snapshot_replays():
    from pathlib import Path
    from pydantic import TypeAdapter
    from ai_quant_os.counterfactual_results import CounterfactualReport
    from ai_quant_os.research_store import encode_research

    raw = (Path(__file__).parents[1] / "docs/evaluations/counterfactual-baseline.json").read_bytes()
    report = TypeAdapter(CounterfactualReport).validate_json(raw)
    assert report.outcome == "COMPLETED"
    assert encode_research(report) == raw

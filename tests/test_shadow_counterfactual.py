import pytest

pytest.importorskip("nautilus_trader")


def test_forward_counterfactual_replays_exact_shadow_source_and_retains_result(
    tmp_path, monkeypatch
):
    from test_shadow_portfolio import forward_bars, shadow_fixture
    from ai_quant_os.broker import SimulatedBroker
    from ai_quant_os.counterfactual_contracts import ReplayVariant
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_counterfactual import (
        ShadowCounterfactualPlan,
        ShadowCounterfactualInputs,
        ShadowCounterfactualStore,
        run_shadow_counterfactual,
    )

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, shadow_store, candidates, strategies)
    bars = forward_bars(assignment)
    report = observe_shadow(
        assignment.shadow_id, "forward-source", bars, shadow_store, candidates, strategies
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Forward counterfactual must not place broker orders")

    monkeypatch.setattr(SimulatedBroker, "place", forbidden)
    holdout = assignment.candidate.report.holdout
    variant = ReplayVariant("half-size", holdout.binding.genome, 50, holdout.result.costs)
    plan = ShadowCounterfactualPlan(
        "forward-replay",
        assignment.shadow_id,
        report.sequence,
        report.fingerprint,
        (variant,),
    )
    inputs = ShadowCounterfactualInputs(plan, report)
    store = ShadowCounterfactualStore(tmp_path / "shadow-counterfactual.sqlite3")
    replay = run_shadow_counterfactual(inputs, strategies, store)

    assert replay.outcome == "COMPLETED"
    assert replay.baseline == report.run
    assert replay.source_bars == report.source_bars
    assert replay.results[0].variant == variant
    assert [fill.quantity for fill in replay.results[0].run.result.fills] == [50, 50]
    assert replay.results[0].pnl_difference == (
        replay.results[0].run.result.net_pnl - report.run.result.net_pnl
    )
    assert replay.promotion_allowed is False
    assert replay.causal_attribution_allowed is False
    assert candidates.get(assignment.candidate.manifest.candidate_id) == assignment.candidate_view
    assert (
        ShadowCounterfactualStore(tmp_path / "shadow-counterfactual.sqlite3").get(plan.replay_id)
        == replay
    )


def test_forward_counterfactual_requires_exact_retained_shadow_report(tmp_path):
    from dataclasses import replace

    from test_shadow_portfolio import forward_bars, shadow_fixture
    from ai_quant_os.counterfactual_contracts import ReplayVariant
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_counterfactual import (
        ShadowCounterfactualInputs,
        ShadowCounterfactualPlan,
    )

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, shadow_store, candidates, strategies)
    report = observe_shadow(
        assignment.shadow_id,
        "bound-source",
        forward_bars(assignment),
        shadow_store,
        candidates,
        strategies,
    )
    holdout = assignment.candidate.report.holdout
    from ai_quant_os.strategy_registry import StrategyRegistry

    StrategyRegistry(tmp_path / "strategies").register(holdout.binding.implementation)
    variant = ReplayVariant("half-size", holdout.binding.genome, 50, holdout.result.costs)
    plan = ShadowCounterfactualPlan(
        "bound-replay", assignment.shadow_id, report.sequence, report.fingerprint, (variant,)
    )

    with pytest.raises(ValueError, match="source report"):
        ShadowCounterfactualInputs(plan, replace(report, sequence=report.sequence + 1))


def test_forward_counterfactual_reservation_is_atomic_for_duplicate_workers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from test_shadow_portfolio import forward_bars, shadow_fixture
    from ai_quant_os.counterfactual_contracts import ReplayVariant
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_counterfactual import (
        ShadowCounterfactualConflictError,
        ShadowCounterfactualInputs,
        ShadowCounterfactualPlan,
        ShadowCounterfactualStore,
    )

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, shadow_store, candidates, strategies)
    report = observe_shadow(
        assignment.shadow_id,
        "concurrent-source",
        forward_bars(assignment),
        shadow_store,
        candidates,
        strategies,
    )
    holdout = assignment.candidate.report.holdout
    variant = ReplayVariant("half-size", holdout.binding.genome, 50, holdout.result.costs)
    inputs = ShadowCounterfactualInputs(
        ShadowCounterfactualPlan(
            "shared-replay", assignment.shadow_id, report.sequence, report.fingerprint, (variant,)
        ),
        report,
    )
    store = ShadowCounterfactualStore(tmp_path / "shadow-counterfactual.sqlite3")
    barrier = Barrier(2)

    def reserve():
        barrier.wait()
        try:
            return store.reserve(inputs)
        except ShadowCounterfactualConflictError:
            return "IN_FLIGHT"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: reserve(), range(2)))

    assert sum(isinstance(result, tuple) and result[0] is not None for result in results) == 1
    assert results.count("IN_FLIGHT") == 1


def test_forward_counterfactual_persists_baseline_execution_failure_for_safe_retry(
    tmp_path, monkeypatch
):
    from test_shadow_portfolio import forward_bars, shadow_fixture
    from ai_quant_os.counterfactual_contracts import ReplayVariant
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_counterfactual import (
        ShadowCounterfactualInputs,
        ShadowCounterfactualPlan,
        ShadowCounterfactualStore,
        run_shadow_counterfactual,
    )

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, shadow_store, candidates, strategies)
    report = observe_shadow(
        assignment.shadow_id,
        "failed-baseline-source",
        forward_bars(assignment),
        shadow_store,
        candidates,
        strategies,
    )
    holdout = assignment.candidate.report.holdout
    variant = ReplayVariant("half-size", holdout.binding.genome, 50, holdout.result.costs)
    inputs = ShadowCounterfactualInputs(
        ShadowCounterfactualPlan(
            "failed-baseline-replay",
            assignment.shadow_id,
            report.sequence,
            report.fingerprint,
            (variant,),
        ),
        report,
    )
    store = ShadowCounterfactualStore(tmp_path / "shadow-counterfactual.sqlite3")

    def fail_baseline(*args, **kwargs):
        raise RuntimeError("simulated native failure")

    monkeypatch.setattr("ai_quant_os.shadow_counterfactual.run_registered_backtest", fail_baseline)
    failed = run_shadow_counterfactual(inputs, strategies, store)
    retried = run_shadow_counterfactual(inputs, strategies, store)

    assert failed.outcome == "FAILED"
    assert failed.reason == "NATIVE_EXECUTION_FAILURE"
    assert failed.baseline is None
    assert failed.results == ()
    assert retried == failed


def test_protected_api_runs_and_reuses_forward_counterfactual_from_verified_shadow_source(
    tmp_path,
):
    from dataclasses import asdict
    import json

    from test_shadow_portfolio import forward_bars, shadow_fixture
    from ai_quant_os.api import create_app
    from ai_quant_os.counterfactual_contracts import ReplayVariant
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from fastapi.testclient import TestClient

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, shadow_store, candidates, strategies)
    report = observe_shadow(
        assignment.shadow_id,
        "api-source",
        forward_bars(assignment),
        shadow_store,
        candidates,
        strategies,
    )
    holdout = assignment.candidate.report.holdout
    variant = ReplayVariant("half-size", holdout.binding.genome, 50, holdout.result.costs)
    payload = {
        "replay_id": "api-forward-replay",
        "sequence": report.sequence,
        "report_sha256": report.fingerprint,
        "variants": [
            {
                "name": variant.name,
                "genome": asdict(variant.genome),
                "quantity": variant.quantity,
                "costs": {
                    "slippage_bps": str(variant.costs.slippage_bps),
                    "fee_per_trade": str(variant.costs.fee_per_trade),
                },
            }
        ],
    }
    payload = json.loads(json.dumps(payload))
    app = create_app(operator_token="operator", research_workspace=tmp_path)
    with TestClient(app) as client:
        path = f"/api/v1/paper/shadows/{assignment.shadow_id}/counterfactual"
        assert client.post(path, json=payload).status_code == 401
        response = client.post(path, headers={"X-Operator-Token": "operator"}, json=payload)
        replayed = client.post(path, headers={"X-Operator-Token": "operator"}, json=payload)
        changed = json.loads(json.dumps(payload))
        changed["variants"][0]["quantity"] = 40
        conflict = client.post(path, headers={"X-Operator-Token": "operator"}, json=changed)

    assert response.status_code == 200
    assert response.json()["outcome"] == "COMPLETED"
    assert response.json()["inputs"]["plan"]["report_sha256"] == report.fingerprint
    assert response.json()["results"][0]["variant"]["name"] == "half-size"
    assert replayed.json() == response.json()
    assert conflict.status_code == 409
    from pathlib import Path
    from jsonschema import validate

    schemas = Path(__file__).resolve().parents[1] / "docs/schemas"
    validate(
        payload, json.loads((schemas / "shadow-counterfactual-input-v1.schema.json").read_text())
    )
    validate(
        response.json(),
        json.loads((schemas / "shadow-counterfactual-output-v1.schema.json").read_text()),
    )

    import sqlite3

    with sqlite3.connect(tmp_path / "shadow-counterfactual.sqlite3") as database:
        database.execute(
            "UPDATE shadow_counterfactuals SET report=? WHERE replay_id=?",
            (b"corrupted", "api-forward-replay"),
        )
    with TestClient(app) as client:
        corrupted = client.post(path, headers={"X-Operator-Token": "operator"}, json=payload)
    assert corrupted.status_code == 503

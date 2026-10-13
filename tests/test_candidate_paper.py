from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytest.importorskip("nautilus_trader")


def test_candidate_paper_assignment_binds_exact_frozen_genome_and_intent(tmp_path):
    from test_shadow_portfolio import shadow_fixture
    from ai_quant_os.candidate_paper import create_candidate_paper_assignment
    from ai_quant_os.contracts import TradeIntent

    shadow, _, candidates, _ = shadow_fixture(tmp_path)
    view, bundle = candidates.snapshot(shadow.candidate.manifest.candidate_id)
    strategy_id, strategy_version = bundle.report.holdout.binding.genome.strategy_ref.split("@")
    assigned_at = datetime.now(timezone.utc)
    assignment = create_candidate_paper_assignment(
        "paper-candidate-1",
        view,
        bundle,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        instrument_id=bundle.report.plan.holdout_scope.instrument_id,
        max_exposure=Decimal("500"),
        assigned_at=assigned_at,
        expires_at=assigned_at + timedelta(days=7),
    )
    intent = TradeIntent(
        intent_id="paper-candidate-intent",
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        instrument_id=assignment.instrument_id,
        side="BUY",
        desired_exposure=Decimal("100"),
        risk_budget=Decimal("10"),
        max_spread=Decimal("0.001"),
        max_slippage=Decimal("0.001"),
        valid_until=assigned_at + timedelta(hours=1),
        world_state_version="paper-world-1",
        evidence_refs=("candidate:" + assignment.candidate_id,),
        created_at=assigned_at,
    )

    assignment.authorize(intent, assignment.assignment_id, view, bundle, now=assigned_at)
    with pytest.raises(ValueError, match="assignment"):
        assignment.authorize(
            replace(intent, desired_exposure=Decimal("501")),
            assignment.assignment_id,
            view,
            bundle,
            now=assigned_at,
        )
    withdrawn = candidates.withdraw(
        assignment.candidate_id,
        expected_revision=view.revision,
        command_id="withdraw-paper-candidate",
        reason="Candidate withdrawn before paper order admission",
    )
    with pytest.raises(ValueError, match="lifecycle"):
        assignment.authorize(intent, assignment.assignment_id, withdrawn, bundle, now=assigned_at)


def test_candidate_paper_assignment_is_durable_and_immutable(tmp_path):
    from test_shadow_portfolio import shadow_fixture
    from ai_quant_os.candidate_paper import create_candidate_paper_assignment
    from ai_quant_os.order_store import PaperOrderStore

    shadow, _, candidates, _ = shadow_fixture(tmp_path)
    view, bundle = candidates.snapshot(shadow.candidate.manifest.candidate_id)
    strategy_id, strategy_version = bundle.report.holdout.binding.genome.strategy_ref.split("@")
    now = datetime.now(timezone.utc)
    assignment = create_candidate_paper_assignment(
        "paper-candidate-durable",
        view,
        bundle,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        instrument_id=bundle.report.plan.holdout_scope.instrument_id,
        max_exposure=Decimal("500"),
        assigned_at=now,
        expires_at=now + timedelta(days=1),
    )
    store = PaperOrderStore(tmp_path / "paper.sqlite3")
    assert store.save_candidate_paper_assignment(assignment) == assignment
    restarted = PaperOrderStore(tmp_path / "paper.sqlite3")
    assert restarted.candidate_paper_assignment(assignment.assignment_id) == assignment
    with pytest.raises(ValueError, match="immutable"):
        restarted.save_candidate_paper_assignment(replace(assignment, max_exposure=Decimal("400")))


def test_candidate_assignment_is_enforced_and_retained_with_paper_order(tmp_path):
    from test_shadow_portfolio import shadow_fixture
    from ai_quant_os.candidate_paper import create_candidate_paper_assignment
    from ai_quant_os.contracts import TradeIntent
    from ai_quant_os.execution import PaperExecutionGateway
    from ai_quant_os.market import MarketSnapshot
    from ai_quant_os.risk import RiskKernel, RiskLimits

    shadow, _, candidates, _ = shadow_fixture(tmp_path)
    view, bundle = candidates.snapshot(shadow.candidate.manifest.candidate_id)
    strategy_id, strategy_version = bundle.report.holdout.binding.genome.strategy_ref.split("@")
    now = datetime.now(timezone.utc)
    assignment = create_candidate_paper_assignment(
        "paper-candidate-order",
        view,
        bundle,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        instrument_id=bundle.report.plan.holdout_scope.instrument_id,
        max_exposure=Decimal("500"),
        assigned_at=now,
        expires_at=now + timedelta(days=1),
    )
    risk = RiskKernel(RiskLimits(Decimal("1000"), Decimal("1500"), 5))
    gateway = PaperExecutionGateway(risk, store_path=tmp_path / "paper-orders.sqlite3")
    gateway.save_candidate_paper_assignment(assignment)
    intent = TradeIntent(
        intent_id="paper-candidate-order-intent",
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        instrument_id=assignment.instrument_id,
        side="BUY",
        desired_exposure=Decimal("100"),
        risk_budget=Decimal("10"),
        max_spread=Decimal("0.001"),
        max_slippage=Decimal("0.001"),
        valid_until=now + timedelta(minutes=1),
        world_state_version="paper-world-1",
        evidence_refs=("candidate:" + assignment.candidate_id,),
        created_at=now,
    )
    market = MarketSnapshot(
        assignment.instrument_id,
        Decimal("1.1000"),
        Decimal("1.1001"),
        now,
        "paper-world-1",
        trusted=True,
    )
    decision = risk.evaluate(intent, market, now=now)
    with pytest.raises(ValueError, match="assignment"):
        gateway.submit(
            replace(intent, desired_exposure=Decimal("501")),
            decision,
            idempotency_key="paper-candidate-over-cap",
            now=now,
            candidate_assignment_id=assignment.assignment_id,
        )
    order = gateway.submit(
        intent,
        decision,
        idempotency_key="paper-candidate-order-key",
        now=now,
        candidate_assignment_id=assignment.assignment_id,
    )
    owner = gateway.execution_evidence_ledger[0][0]
    assert order is not None
    assert owner.candidate_assignment_id == assignment.assignment_id
    assert owner.candidate_id == assignment.candidate_id
    assert owner.genome_sha256 == assignment.genome_sha256
    with pytest.raises(ValueError, match="different intent"):
        gateway.replay(intent, idempotency_key="paper-candidate-order-key")


def test_candidate_paper_api_binds_orders_and_rechecks_candidate_lifecycle(tmp_path, monkeypatch):
    from test_research_candidates import archived_workspace
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_candidates import build_candidate_bundle
    from ai_quant_os.api import create_app
    from fastapi.testclient import TestClient

    research_workspace = tmp_path / "research-workspace"
    research_workspace.mkdir()
    report, research, strategies = archived_workspace(research_workspace)
    candidate = build_candidate_bundle(research, strategies, report.plan.experiment_id)
    candidate_store = CandidateStore(research_workspace / "candidates.sqlite3")
    candidate_store.register(candidate)
    strategy_id, strategy_version = candidate.report.holdout.binding.genome.strategy_ref.split("@")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = [now]
    headers = {"X-Operator-Token": "operator", "Idempotency-Key": "candidate-order-key"}
    app = create_app(
        operator_token="operator",
        clock=lambda: clock[0],
        paper_db_path=tmp_path / "paper.sqlite3",
        research_workspace=research_workspace,
    )
    with TestClient(app) as client:
        assignment_payload = {
            "assignment_id": "candidate-paper-api",
            "candidate_id": candidate.manifest.candidate_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "instrument_id": report.plan.holdout_scope.instrument_id,
            "max_exposure": "500",
            "expires_at": (now + timedelta(days=1)).isoformat(),
        }
        assignment_response = client.post(
            "/api/v1/paper/candidate-assignments",
            headers=headers,
            json=assignment_payload,
        )
        assert assignment_response.status_code == 200, assignment_response.text
        assert assignment_response.json()["broker_route"] == "PAPER"
        assert (
            assignment_response.json()["genome_sha256"]
            == candidate.report.holdout.binding.genome.fingerprint
        )
        import json
        from jsonschema import Draft202012Validator
        from pathlib import Path

        assignment_schema = json.loads(
            (
                Path(__file__).parents[1] / "docs/schemas/candidate-paper-assignment-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(assignment_schema).validate(assignment_response.json())
        clock[0] += timedelta(seconds=1)
        retried_assignment = client.post(
            "/api/v1/paper/candidate-assignments", headers=headers, json=assignment_payload
        )
        assert retried_assignment.status_code == 200, retried_assignment.text
        assert retried_assignment.json() == assignment_response.json()
        now = clock[0]
        quote = {
            "instrument_id": "EURUSD",
            "bid": "1.1000",
            "ask": "1.1001",
            "observed_at": now.isoformat(),
            "world_state_version": "world-1",
        }
        assert client.post("/api/v1/paper/quotes", headers=headers, json=quote).status_code == 200
        intent = {
            "intent_id": "candidate-paper-api-intent",
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "instrument_id": "EURUSD",
            "side": "BUY",
            "desired_exposure": "100",
            "risk_budget": "10",
            "max_spread": "0.0003",
            "max_slippage": "0.0002",
            "valid_until": (now + timedelta(minutes=1)).isoformat(),
            "world_state_version": "world-1",
            "evidence_refs": ["fixture:candidate"],
            "created_at": now.isoformat(),
            "candidate_assignment_id": "candidate-paper-api",
        }
        import threading
        from concurrent.futures import ThreadPoolExecutor

        snapshot_entered = threading.Event()
        release_snapshot = threading.Event()
        from ai_quant_os.research_candidate_store import CandidateStore

        from contextlib import contextmanager

        original_admission_snapshot = CandidateStore.admission_snapshot

        @contextmanager
        def paused_admission_snapshot(self, candidate_id):
            with original_admission_snapshot(self, candidate_id) as snapshot:
                snapshot_entered.set()
                assert release_snapshot.wait(timeout=5)
                yield snapshot

        monkeypatch.setattr(CandidateStore, "admission_snapshot", paused_admission_snapshot)
        withdrawal_store = CandidateStore(research_workspace / "candidates.sqlite3")
        with ThreadPoolExecutor(max_workers=2) as pool:
            order_future = pool.submit(
                client.post, "/api/v1/paper/intents", headers=headers, json=intent
            )
            assert snapshot_entered.wait(timeout=5)
            withdrawal_future = pool.submit(
                withdrawal_store.withdraw,
                candidate.manifest.candidate_id,
                expected_revision=1,
                command_id="withdraw-api-candidate",
                reason="Candidate withdrawn after in-flight order admission",
            )
            assert not withdrawal_future.done()
            release_snapshot.set()
            order_response = order_future.result(timeout=5)
            withdrawn = withdrawal_future.result(timeout=5)
        monkeypatch.setattr(CandidateStore, "admission_snapshot", original_admission_snapshot)
        assert order_response.status_code == 200, order_response.text
        assert order_response.json()["broker_route"] == "PAPER"
        assert withdrawn.state == "WITHDRAWN"

        from ai_quant_os.execution import PaperExecutionGateway
        from ai_quant_os.risk import RiskKernel, RiskLimits

        gateway = PaperExecutionGateway(
            RiskKernel(RiskLimits(Decimal("1000"), Decimal("1500"), 5)),
            store_path=tmp_path / "paper.sqlite3",
        )
        owner = gateway.execution_evidence_ledger[0][0]
        assert owner.candidate_assignment_id == "candidate-paper-api"
        assert owner.candidate_id == candidate.manifest.candidate_id
        next_intent = {**intent, "intent_id": "candidate-paper-api-intent-2"}
        next_response = client.post(
            "/api/v1/paper/intents",
            headers={**headers, "Idempotency-Key": "candidate-order-key-2"},
            json=next_intent,
        )
        assert next_response.status_code == 409
        missing_assignment = {**next_intent, "intent_id": "missing-candidate-assignment-intent"}
        missing_assignment["candidate_assignment_id"] = "candidate-assignment-not-found"
        missing_response = client.post(
            "/api/v1/paper/intents",
            headers={**headers, "Idempotency-Key": "missing-candidate-assignment-key"},
            json=missing_assignment,
        )
        assert missing_response.status_code == 409


def test_candidate_paper_assignment_request_schema_is_published():
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from ai_quant_os.api import CandidatePaperAssignmentInput

    schema_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "schemas"
        / "candidate-paper-assignment-input-v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    expected = CandidatePaperAssignmentInput.model_json_schema(mode="validation")
    expected.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:ai-quant-os:contract:candidate-paper-assignment-input:1.0",
        }
    )
    assert schema == expected
    Draft202012Validator.check_schema(schema)

    from pydantic import TypeAdapter
    from ai_quant_os.candidate_paper import CandidatePaperAssignment

    output_path = schema_path.with_name("candidate-paper-assignment-v1.schema.json")
    output_schema = json.loads(output_path.read_text(encoding="utf-8"))
    expected_output = TypeAdapter(CandidatePaperAssignment).json_schema(mode="validation")
    expected_output.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:ai-quant-os:contract:candidate-paper-assignment:1.0",
        }
    )
    assert output_schema == expected_output
    Draft202012Validator.check_schema(output_schema)

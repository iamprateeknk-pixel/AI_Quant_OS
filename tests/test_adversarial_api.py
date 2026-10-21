"""A11 checks completed archived research through a protected sandbox graph."""

from datetime import datetime, timezone
import json
from pathlib import Path
from hashlib import sha256

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from ai_quant_os.api import create_app
from ai_quant_os.research_results import ResearchReport
from ai_quant_os.research_store import ResearchStore
from ai_quant_os.holdout_validator_rpc import (
    HoldoutResponse,
    stress_summary_sha256,
)

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-a11"}


def report_fixture():
    payload = json.loads(
        (
            Path(__file__).parents[1] / "docs/evaluations/research-adversarial-baseline.json"
        ).read_text()
    )
    return TypeAdapter(ResearchReport).validate_python(payload["report"])


def settings(tmp_path, clock=lambda: NOW, holdout_rpc_client=None):
    workspace = tmp_path / "research"
    workspace.mkdir(exist_ok=True)
    return dict(
        operator_token="fixture-a11",
        clock=clock,
        paper_db_path=tmp_path / "paper.sqlite3",
        research_workspace=workspace,
        holdout_rpc_client=holdout_rpc_client,
    )


def service_client(report):
    scenarios = tuple(
        {
            "name": row.name,
            "dataset_fingerprint": row.dataset_fingerprint,
            "net_pnl": str(row.run.result.net_pnl),
            "pnl_currency": row.run.result.pnl_currency,
            "native_order_count": row.run.result.native_order_count,
        }
        for row in report.adversarial.scenarios
    )
    digest = sha256(
        __import__("ai_quant_os.research_store", fromlist=["encode_research"]).encode_research(
            report
        )
    ).hexdigest()

    class Client:
        def call(self, request):
            summary = stress_summary_sha256(
                report.adversarial.state, report.adversarial.reason, scenarios
            )
            if request.operation == "STRESS_CONTEXT":
                from ai_quant_os.adversarial_agent import adversarial_context_snapshot

                assert request.candidate_ref is request.training_ref is request.holdout_ref is None
                assert request.evidence_ref == report.plan.experiment_id
                snapshot = adversarial_context_snapshot(report, now=NOW)
                return HoldoutResponse.model_validate_json(
                    json.dumps(
                        {
                            "request_id": request.request_id,
                            "task_id": request.task_id,
                            "context_ref": request.context_ref,
                            "status": "CHECKED_ARCHIVED_STRESS_CONTEXT",
                            "evidence_ref": request.evidence_ref,
                            "summary_sha256": summary,
                            "aggregate": {
                                "state": report.adversarial.state,
                                "reason_codes": [report.adversarial.reason],
                                "scenario_count": len(scenarios),
                                "scenarios": scenarios,
                            },
                            "context_snapshot": {
                                "agent_id": "A11",
                                "payload_json": snapshot.model_dump_json(),
                            },
                        }
                    )
                )
            assert request.operation == "STRESS_RUN"
            assert request.candidate_ref == report.plan.experiment_id
            assert request.training_ref == (
                "research-data-scope:" + report.plan.development_fingerprint
            )
            assert request.holdout_ref == "research-data-scope:" + report.plan.holdout_fingerprint
            assert request.evidence_ref.endswith(digest)
            return HoldoutResponse.model_validate_json(
                json.dumps(
                    {
                        "request_id": request.request_id,
                        "task_id": request.task_id,
                        "context_ref": request.context_ref,
                        "status": "CHECKED_ARCHIVED_STRESS",
                        "evidence_ref": request.evidence_ref,
                        "summary_sha256": summary,
                        "aggregate": {
                            "state": report.adversarial.state,
                            "reason_codes": [report.adversarial.reason],
                            "scenario_count": len(scenarios),
                            "scenarios": scenarios,
                        },
                    }
                )
            )

    return Client()


def test_a11_graph_verifies_archived_adversarial_results_and_cannot_trade(tmp_path):
    report = report_fixture()

    with TestClient(
        create_app(**settings(tmp_path, holdout_rpc_client=service_client(report)))
    ) as client:
        before = client.get("/api/v1/system/status").json()
        context_response = client.post(
            "/api/v1/sandbox/adversarial-validator/context",
            headers=HEADERS,
            json={"task_id": "adversarial-review", "experiment_id": report.plan.experiment_id},
        )
        assert context_response.status_code == 200, context_response.text
        context = context_response.json()
        assert context["scope"] == "SANDBOX" and context["status"] == "READY"
        executed = client.post(
            "/api/v1/sandbox/adversarial-validator/execute",
            headers=HEADERS,
            json={"run_id": "adversarial-review", "context_ref": context["context_ref"]},
        )
        assert executed.status_code == 200, executed.text
        receipt = executed.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["agent_id"] == "A11" and output["agent_version"] == "0.2.0"
        assert output["adversarial_score"] == report.adversarial.state
        assert output["assessment"] == "NO_FAILURE_FOUND"
        assert len(output["scenarios"]) == len(report.adversarial.scenarios)
        assert output["promotion_allowed"] is False
        assert output["requires_escalation"] is True
        assert client.get("/api/v1/system/status").json() == before
        assert (
            client.post(
                "/api/v1/paper/agents/execute",
                headers=HEADERS,
                json={"run_id": "wrong-scope", "context_ref": context["context_ref"]},
            ).status_code
            == 403
        )


def test_a11_routes_require_operator_and_reject_unknown_experiments(tmp_path):
    options = settings(tmp_path)
    ResearchStore(options["research_workspace"] / "research.sqlite3")
    with TestClient(create_app(**options)) as client:
        denied = client.post(
            "/api/v1/sandbox/adversarial-validator/context",
            json={"task_id": "review", "experiment_id": "not-found"},
        )
        assert denied.status_code in (401, 403)
        missing = client.post(
            "/api/v1/sandbox/adversarial-validator/context",
            headers=HEADERS,
            json={"task_id": "review", "experiment_id": "not-found"},
        )
        assert missing.status_code == 503


def test_a11_reports_native_adversarial_failure_as_a_rejection(tmp_path):
    import pytest

    pytest.importorskip("nautilus_trader")
    from decimal import Decimal
    from test_research_pipeline import plan
    from ai_quant_os.adversarial import AdversarialSpec
    from ai_quant_os.backtest import BacktestCosts
    from ai_quant_os.market_data import import_bars_csv
    from ai_quant_os.monte_carlo import MonteCarloSpec
    from ai_quant_os.research_pipeline import run_research_experiment
    from ai_quant_os.strategy_registry import StrategyRegistry

    fixture_dir = Path(__file__).parent / "fixtures"
    development = import_bars_csv(fixture_dir / "research_mc_development.csv")
    holdout = import_bars_csv(fixture_dir / "research_mc_holdout.csv")
    declared = plan(
        development,
        holdout,
        train_bars=20,
        validation_bars=20,
        schema_version="1.2",
        stress_costs=BacktestCosts(Decimal(100), Decimal(10)),
        monte_carlo_spec=MonteCarloSpec(iterations=100),
        adversarial_spec=AdversarialSpec(maximum_loss=Decimal(0)),
    )
    workspace = tmp_path / "research"
    workspace.mkdir()
    store = ResearchStore(workspace / "research.sqlite3")
    report = run_research_experiment(
        declared, development, holdout, StrategyRegistry(workspace / "strategies"), store
    )
    assert report.adversarial.state == "FAIL"

    with TestClient(
        create_app(**settings(tmp_path, holdout_rpc_client=service_client(report)))
    ) as client:
        context = client.post(
            "/api/v1/sandbox/adversarial-validator/context",
            headers=HEADERS,
            json={"task_id": "adversarial-failure", "experiment_id": declared.experiment_id},
        )
        assert context.status_code == 200, context.text
        response = client.post(
            "/api/v1/sandbox/adversarial-validator/execute",
            headers=HEADERS,
            json={
                "run_id": "adversarial-failure",
                "context_ref": context.json()["context_ref"],
            },
        )
        assert response.status_code == 200, response.text
        receipt = response.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["adversarial_score"] == "FAIL"
        assert output["assessment"] == "FAILURE_FOUND"
        assert output["rejection_reasons"] == ["DECLARED_SCENARIO_LOSS_EXCEEDED"]
        assert output["promotion_allowed"] is False

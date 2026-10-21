"""A12 stays in evidence-withholding mode until candidate gates are connected."""

from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.research_candidate_store import CandidateStore

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-a12"}


def settings(tmp_path, *, initialize_registry=True):
    workspace = tmp_path / "research"
    workspace.mkdir()
    if initialize_registry:
        CandidateStore(workspace / "candidates.sqlite3")
    return dict(
        operator_token="fixture-a12",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
        research_workspace=workspace,
    )


def test_a12_runs_as_shadow_selector_and_withholds_eligibility(tmp_path):
    with TestClient(create_app(**settings(tmp_path))) as client:
        before = client.get("/api/v1/system/status").json()
        context_response = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A12", "agent_version": "0.2.0", "task_id": "selector"},
        )
        assert context_response.status_code == 200, context_response.text
        context = context_response.json()
        assert context["status"] == "READY"
        assert [row["input_name"] for row in context["inputs"]] == [
            "approved_strategy_registry",
            "world_state",
            "performance",
            "drift",
        ]
        executed = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "selector", "context_ref": context["context_ref"]},
        )
        assert executed.status_code == 200, executed.text
        receipt = executed.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["agent_id"] == "A12" and output["agent_version"] == "0.2.0"
        assert output["state"] == "WAIT_FOR_EVIDENCE"
        assert output["eligible_strategies"] == []
        assert output["performance_available"] is False
        assert output["drift_available"] is False
        assert output["promotion_allowed"] is False
        assert "NO_APPROVED_CANDIDATES" in output["blockers"]
        assert client.get("/api/v1/system/status").json() == before


def test_a12_v3_binds_shadow_performance_and_keeps_eligibility_fail_closed(tmp_path):
    with TestClient(create_app(**settings(tmp_path))) as client:
        compiled = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A12", "agent_version": "0.3.0", "task_id": "selector-v3"},
        )
        assert compiled.status_code == 200, compiled.text
        context = compiled.json()
        performance = next(row for row in context["inputs"] if row["input_name"] == "performance")
        assert performance["status"] == "AVAILABLE"
        assert json.loads(performance["payload_json"])["availability"] == "UNAVAILABLE"
        executed = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "selector-v3", "context_ref": context["context_ref"]},
        )
        assert executed.status_code == 200, executed.text
        receipt = executed.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["agent_version"] == "0.3.0"
        assert output["eligible_strategies"] == []
        assert output["performance_available"] is False
        assert output["drift_available"] is False
        assert output["promotion_allowed"] is False


def test_a12_context_and_execution_require_operator(tmp_path):
    with TestClient(create_app(**settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/paper/agents/context",
            json={"agent_id": "A12", "agent_version": "0.2.0", "task_id": "selector"},
        )
        assert response.status_code in (401, 403)


def test_a12_rejects_registry_change_after_context_compilation(tmp_path):
    options = settings(tmp_path, initialize_registry=False)
    with TestClient(create_app(**options)) as client:
        compiled = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A12", "agent_version": "0.2.0", "task_id": "selector-change"},
        )
        assert compiled.status_code == 200, compiled.text
        CandidateStore(options["research_workspace"] / "candidates.sqlite3")
        executed = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "selector-change", "context_ref": compiled.json()["context_ref"]},
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["receipt"]["status"] == "INVALID"

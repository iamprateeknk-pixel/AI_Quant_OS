"""The A13 paper graph reports blockers and leaves paper controls untouched."""

from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-a13"}


def test_a13_returns_no_allocation_when_upstream_and_covariance_are_missing(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-a13",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        before = client.get("/api/v1/system/status").json()
        compiled = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A13", "agent_version": "0.2.0", "task_id": "allocator"},
        )
        assert compiled.status_code == 200, compiled.text
        assert compiled.json()["status"] == "READY"
        assert [row["input_name"] for row in compiled.json()["inputs"]] == [
            "strategy_set",
            "portfolio_snapshot",
            "risk_snapshot",
            "covariance",
            "exposure",
        ]
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "allocator", "context_ref": compiled.json()["context_ref"]},
        )
        assert result.status_code == 200, result.text
        receipt = result.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["state"] == "WAIT_FOR_EVIDENCE"
        assert output["allocations"] == []
        assert output["execution_authorized"] is False
        assert output["promotion_allowed"] is False
        assert output["safety_state"] == "NORMAL"
        assert client.get("/api/v1/system/status").json() == before


def test_a13_v3_binds_a_checked_a12_graph_result_without_authorizing_allocation(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-a13",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        selector_context = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A12", "agent_version": "0.3.0", "task_id": "selector"},
        )
        assert selector_context.status_code == 200, selector_context.text
        selector_run = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "selector-run", "context_ref": selector_context.json()["context_ref"]},
        )
        assert selector_run.status_code == 200, selector_run.text
        assert selector_run.json()["receipt"]["status"] == "COMPLETED"

        allocator_context = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={
                "agent_id": "A13",
                "agent_version": "0.3.0",
                "task_id": "allocator",
                "upstream_run_id": "selector-run",
            },
        )
        assert allocator_context.status_code == 200, allocator_context.text
        strategy_set = next(
            row for row in allocator_context.json()["inputs"] if row["input_name"] == "strategy_set"
        )
        payload = json.loads(strategy_set["payload_json"])
        assert payload["availability"] == "AVAILABLE"
        assert payload["upstream_run_id"] == "selector-run"
        assert payload["eligible_candidate_ids"] == []

        allocator_run = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={
                "run_id": "allocator-run",
                "context_ref": allocator_context.json()["context_ref"],
            },
        )
        assert allocator_run.status_code == 200, allocator_run.text
        receipt = allocator_run.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["state"] == "WAIT_FOR_EVIDENCE"
        assert output["allocations"] == []
        assert "COVARIANCE_UNAVAILABLE" in output["blockers"]
        assert "RISK_BUDGET_UNCALIBRATED" in output["blockers"]
        assert output["execution_authorized"] is False
        assert output["promotion_allowed"] is False


def test_a13_v3_rejects_unrelated_or_incomplete_selector_runs(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-a13",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        missing = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A13", "agent_version": "0.3.0", "task_id": "allocator"},
        )
        assert missing.status_code == 422

        unknown = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={
                "agent_id": "A13",
                "agent_version": "0.3.0",
                "task_id": "allocator",
                "upstream_run_id": "missing-run",
            },
        )
        assert unknown.status_code == 404

        selector_context = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A12", "agent_version": "0.2.0", "task_id": "selector"},
        )
        selector_run = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={
                "run_id": "selector-v2-run",
                "context_ref": selector_context.json()["context_ref"],
            },
        )
        assert selector_run.status_code == 200, selector_run.text
        assert selector_run.json()["receipt"]["status"] == "COMPLETED"

        invalid = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={
                "agent_id": "A13",
                "agent_version": "0.3.0",
                "task_id": "allocator",
                "upstream_run_id": "selector-v2-run",
            },
        )
        assert invalid.status_code == 409

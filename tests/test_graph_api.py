from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-operator"}


def test_graph_requires_operator_and_persistent_configuration(tmp_path):
    app = create_app(operator_token="fixture-operator", clock=lambda: NOW)
    with TestClient(app) as client:
        body = {"run_id": "run-1", "context_ref": "context:unknown"}
        assert client.post("/api/v1/paper/agents/execute", json=body).status_code == 401
        assert (
            client.post("/api/v1/paper/agents/execute", json=body, headers=HEADERS).status_code
            == 503
        )


def test_graph_api_uses_archived_context_and_retains_failure_on_restart(tmp_path):
    path = tmp_path / "paper.sqlite3"
    args = dict(operator_token="fixture-operator", clock=lambda: NOW, paper_db_path=path)
    with TestClient(create_app(**args)) as client:
        response = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={
                "agent_id": "A02",
                "agent_version": "0.1.0",
                "task_id": "task",
            },
        )
        assert response.status_code == 200
        ref = response.json()["context_ref"]
        response = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "run-1", "context_ref": ref},
        )
        assert response.status_code == 200
        receipt = response.json()["receipt"]
        assert receipt["status"] == "WAIT_FOR_DATA"
        assert receipt["context_ref"] == ref
        assert receipt["output_json"] is None
        assert client.get("/api/v1/agents/graph-runs/run-1").status_code == 401
    with TestClient(create_app(**args)) as client:
        response = client.get("/api/v1/agents/graph-runs/run-1", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["archival_only"] is True
        assert response.json()["receipt"] == receipt
        response = client.post("/api/v1/paper/agents/graph-runs/run-1/resume", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["receipt"] == receipt
        assert client.get("/api/v1/agents/graph-runs/missing", headers=HEADERS).status_code == 404
    args["clock"] = lambda: receipt_time(receipt)
    with TestClient(create_app(**args)) as client:
        response = client.get("/api/v1/agents/graph-runs/run-1", headers=HEADERS)
        assert response.json()["context_current"] is False
        assert response.json()["receipt"] == receipt


def receipt_time(receipt):
    return datetime.fromisoformat(receipt["valid_until"].replace("Z", "+00:00"))

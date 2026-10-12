"""A17 exposes a read-only evolution readiness report through the paper graph."""

from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-a17"}


def test_a17_returns_no_tournament_actions_or_promotion_authority(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-a17",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        before = client.get("/api/v1/system/status").json()
        context = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A17", "agent_version": "0.2.0", "task_id": "evolution-review"},
        )
        assert context.status_code == 200, context.text
        assert context.json()["status"] == "READY"
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "evolution-review", "context_ref": context.json()["context_ref"]},
        )
        assert result.status_code == 200, result.text
        receipt = result.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["state"] == "WAIT_FOR_EVIDENCE"
        assert output["plan_created"] is False
        assert output["tournament_actions"] == []
        assert output["promotion_allowed"] is False
        assert client.get("/api/v1/system/status").json() == before

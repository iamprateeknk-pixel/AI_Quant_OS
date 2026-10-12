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

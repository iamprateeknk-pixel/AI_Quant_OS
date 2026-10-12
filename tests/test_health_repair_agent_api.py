"""A16 exposes a read-only repair readiness report through the protected graph."""

from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-a16"}


def test_a16_reports_readiness_blockers_without_creating_repair_work(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-a16",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        before = client.get("/api/v1/system/status").json()
        compiled = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A16", "agent_version": "0.2.0", "task_id": "repair-review"},
        )
        assert compiled.status_code == 200, compiled.text
        assert compiled.json()["status"] == "READY", [
            (row["input_name"], row["status"], row.get("source_tool"))
            for row in compiled.json()["inputs"]
        ]
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "repair-review", "context_ref": compiled.json()["context_ref"]},
        )
        assert result.status_code == 200, result.text
        receipt = result.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["state"] == "WAIT_FOR_EVIDENCE"
        assert output["repair_candidate_created"] is False
        assert output["repair_plan"] == []
        assert output["promotion_allowed"] is False
        assert client.get("/api/v1/system/status").json() == before

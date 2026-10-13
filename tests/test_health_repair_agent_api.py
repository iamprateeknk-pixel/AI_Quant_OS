"""A16 exposes a read-only repair readiness report through the protected graph."""

from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.health_store import HealthStore

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


def test_a16_v3_protected_graph_reports_incident_context_unavailable_without_watchdog(
    tmp_path,
):
    with TestClient(
        create_app(
            operator_token="fixture-a16",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        compiled = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A16", "agent_version": "0.3.0", "task_id": "repair-v3"},
        )
        assert compiled.status_code == 200, compiled.text
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "repair-v3", "context_ref": compiled.json()["context_ref"]},
        )
        assert result.status_code == 200, result.text
        receipt = result.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["agent_version"] == "0.3.0"
        assert output["incident_context_state"] == "UNAVAILABLE"
        assert output["incident_ids"] == []
        assert "INCIDENT_DETAILS_UNAVAILABLE" in output["blockers"]
        assert output["state"] == "WAIT_FOR_EVIDENCE"
        assert output["repair_candidate_created"] is False
        assert output["promotion_allowed"] is False


def test_a16_v3_reads_verified_bounded_incident_details_from_health_store(tmp_path):
    incident = HealthIncident(
        "QUOTE_STALE", "feed-private-subject", '{"private":"secret-evidence"}', NOW
    )
    store = HealthStore(tmp_path / "paper.health.sqlite3")
    store.record(incident)

    with TestClient(
        create_app(
            operator_token="fixture-a16",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            health_watchdog_enabled=True,
        )
    ) as client:
        compiled = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A16", "agent_version": "0.3.0", "task_id": "repair-live"},
        )
        assert compiled.status_code == 200, compiled.text
        detail_input = next(
            row for row in compiled.json()["inputs"] if row["input_name"] == "incident_context"
        )
        assert incident.incident_id in detail_input["payload_json"]
        assert "secret-evidence" not in detail_input["payload_json"]
        assert "feed-private-subject" not in detail_input["payload_json"]

        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "repair-live", "context_ref": compiled.json()["context_ref"]},
        )
        assert result.status_code == 200, result.text
        output = json.loads(result.json()["receipt"]["output_json"])
        assert output["open_incidents"] == 1
        assert output["incident_context_state"] == "DETAILS"
        assert output["incident_ids"] == [incident.incident_id]
        assert output["blockers"] == [
            "CODE_TEST_EVIDENCE_UNAVAILABLE",
            "TRACE_EVIDENCE_UNAVAILABLE",
        ]
        assert output["state"] == "WAIT_FOR_EVIDENCE"
        assert output["repair_candidate_created"] is False
        assert output["promotion_allowed"] is False

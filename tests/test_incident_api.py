from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.health_store import HealthStore

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-incident"}


def execute(client, run_id="incident-1"):
    context = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={
            "agent_id": "A18",
            "agent_version": "0.2.0",
            "task_id": "incident-review",
        },
    )
    assert context.status_code == 200, context.text
    response = client.post(
        "/api/v1/paper/agents/execute",
        headers=HEADERS,
        json={
            "run_id": run_id,
            "context_ref": context.json()["context_ref"],
        },
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    assert receipt["status"] == "COMPLETED", receipt
    return receipt, json.loads(receipt["output_json"])


def test_missing_safety_evidence_creates_durable_review_without_changing_controls(tmp_path):
    settings = dict(
        operator_token="fixture-incident",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**settings)) as client:
        before = client.get("/api/v1/system/status").json()
        receipt, output = execute(client)
        assert output["recommended_state"] == "NO_NEW_RISK"
        assert output["incident_class"] == "DEGRADED_EVIDENCE"
        assert output["confidence"] == "0" and output["requires_escalation"]
        assert output["workflow_dispatched"] is False
        assert client.get("/api/v1/system/status").json() == before
        proposal_ref = output["proposal_ref"]
        proposal = client.get("/api/v1/paper/incidents/proposals/" + proposal_ref, headers=HEADERS)
        assert proposal.status_code == 200 and proposal.json()["archival_only"]
        assert proposal.json()["proposal"]["recommended_state"] == "NO_NEW_RISK"
        retry = client.post("/api/v1/paper/agents/graph-runs/incident-1/resume", headers=HEADERS)
        assert retry.json()["receipt"] == receipt
    with TestClient(create_app(**settings)) as client:
        assert (
            client.get("/api/v1/paper/incidents/proposals/" + proposal_ref, headers=HEADERS).json()[
                "proposal"
            ]
            == proposal.json()["proposal"]
        )
        assert (
            client.get("/api/v1/agents/graph-runs/incident-1", headers=HEADERS).json()["receipt"]
            == receipt
        )


def test_restored_critical_watchdog_event_cannot_be_recommended_for_recovery(tmp_path):
    store = HealthStore(tmp_path / "paper.health.sqlite3")
    store.record(HealthIncident("POSITION_MISMATCH", "paper-positions", "{}", NOW))
    store._db.close()
    with TestClient(
        create_app(
            operator_token="fixture-incident",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        before = client.get("/api/v1/system/status").json()
        assert before["safety_state"] == "HALT"
        _, output = execute(client)
        assert output["recommended_state"] == "HALT" and output["incident_class"] == "CRITICAL"
        assert output["watchdog_incident_count"] == 1
        assert "POSITION_MISMATCH" in output["findings"]
        assert output["recovery_authorized"] is False
        assert client.get("/api/v1/system/status").json() == before


def test_preview_cannot_create_incident_proposals_without_operator_access(tmp_path):
    with TestClient(create_app(paper_db_path=tmp_path / "paper.sqlite3")) as client:
        assert (
            client.post(
                "/api/v1/paper/agents/context",
                json={
                    "agent_id": "A18",
                    "agent_version": "0.2.0",
                    "task_id": "preview",
                },
            ).status_code
            == 503
        )

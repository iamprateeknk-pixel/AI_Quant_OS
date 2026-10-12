"""Operator review is durable and cannot authorize paper controls."""

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.monitoring_alerts import MonitoringAlertSource, MonitoringObservation

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
HEADERS = {"X-Operator-Token": "fixture-token"}


def selected_observation():
    raw = json.loads(
        (
            Path(__file__).resolve().parents[1] / "apps/web/tests/fixtures/monitoring-alerts.json"
        ).read_text()
    )
    raw["observed_at"] = NOW.isoformat()
    for rule in raw["rules"]:
        rule["evaluated_at"] = NOW.isoformat()
        rule["active_since"] = "2026-10-11T23:57:00+00:00" if rule["state"] != "INACTIVE" else None
    return MonitoringObservation.model_validate(raw)


def test_protected_history_and_acknowledgement_survive_app_restart(tmp_path):
    source = MonitoringAlertSource(enabled=False)

    async def observe():
        return selected_observation()

    source.observe = observe
    database = tmp_path / "paper.sqlite3"
    with TestClient(
        create_app(
            paper_db_path=database,
            operator_token="fixture-token",
            clock=lambda: NOW,
            monitoring_alert_source=source,
        )
    ) as client:
        assert client.get("/api/v1/observability/incidents").status_code == 401
        session = client.post("/api/v1/operator/sessions", headers=HEADERS).json()["session_token"]
        headers = {"X-Operator-Session": session}
        assert client.get("/api/v1/observability/alerts", headers=headers).status_code == 200
        history = client.get("/api/v1/observability/incidents", headers=headers)
        assert history.status_code == 200, history.text
        payload = history.json()
        from jsonschema import Draft202012Validator, FormatChecker

        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "docs/schemas/monitoring-incident-journal-page.schema.json"
            ).read_text()
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        receipt_schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "docs/schemas/monitoring-incident-journal.schema.json"
            ).read_text()
        )
        receipt_validator = Draft202012Validator(receipt_schema, format_checker=FormatChecker())
        validator.validate(payload)
        assert payload["total"] == 1 and payload["offset"] == 0 and payload["limit"] == 50
        assert payload["next_offset"] is None
        assert (
            client.get(
                "/api/v1/observability/incidents?offset=1",
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.get(
                f"/api/v1/observability/incidents?offset=1&head={payload['head_hash']}",
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/observability/incidents?offset=1&head={'0' * 64}",
                headers=headers,
            ).status_code
            == 409
        )
        assert payload["durable"] is True
        assert payload["notification_verified"] is False
        episode = payload["episodes"][0]
        receipt = client.post(
            "/api/v1/observability/incidents/acknowledge",
            headers=headers,
            json={
                "episode_id": episode["episode_id"],
                "action_id": str(uuid4()),
                "expected_head": payload["head_hash"],
            },
        )
        assert receipt.status_code == 200
        receipt_validator.validate(receipt.json())
        assert receipt.json()["committed"] is True
        assert receipt.json()["control_authorized"] is False
        assert receipt.json()["actor_session_id"]
        assert (
            client.get(
                f"/api/v1/observability/incidents?offset=0&head={payload['head_hash']}",
                headers=headers,
            ).status_code
            == 409
        )

    restarted_source = MonitoringAlertSource(enabled=False)
    restarted = TestClient(
        create_app(
            paper_db_path=database,
            operator_token="fixture-token",
            clock=lambda: NOW,
            monitoring_alert_source=restarted_source,
        )
    )
    with restarted:
        session = restarted.post("/api/v1/operator/sessions", headers=HEADERS).json()[
            "session_token"
        ]
        history = restarted.get(
            "/api/v1/observability/incidents", headers={"X-Operator-Session": session}
        ).json()
        assert history["episodes"][0]["acknowledgement"]["control_authorized"] is False

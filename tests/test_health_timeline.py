from datetime import datetime, timedelta, timezone
import sqlite3
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.health_store import HealthStore
from ai_quant_os.health_api import install_health_routes


def seed(path, count=4):
    store = HealthStore(path)
    for index in range(count):
        store.record(
            HealthIncident(
                "QUOTE_STALE",
                f"fixture-{index}",
                "{}",
                datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index),
            )
        )


def test_timeline_pages_keep_one_verified_head_and_no_raw_evidence(tmp_path):
    seed(tmp_path / "paper.health.sqlite3")
    with TestClient(
        create_app(
            operator_token="operator",
            paper_db_path=tmp_path / "paper.sqlite3",
            health_watchdog_enabled=False,
        )
    ) as client:
        url = "/api/v1/paper/health/incidents/page"
        assert client.get(url).status_code == 401
        headers = {"X-Operator-Token": "operator"}
        first = client.get(url, params={"limit": 2}, headers=headers)
        assert first.status_code == 200
        page = first.json()
        assert page["total"] == 4 and page["next_offset"] == 2
        assert [row["subject"] for row in page["incidents"]] == ["fixture-3", "fixture-2"]
        assert all("evidence_json" not in row for row in page["incidents"])
        second = client.get(
            url, params={"limit": 2, "offset": 2, "head": page["head_hash"]}, headers=headers
        ).json()
        assert second["head_hash"] == page["head_hash"]
        assert [row["subject"] for row in second["incidents"]] == ["fixture-1", "fixture-0"]
        assert second["next_offset"] is None
        seed(tmp_path / "paper.health.sqlite3", 5)
        assert (
            client.get(
                url, params={"offset": 2, "head": page["head_hash"]}, headers=headers
            ).status_code
            == 409
        )
        for params in ({"offset": 2}, {"limit": 101}, {"offset": -1}, {"head": "invalid"}):
            assert client.get(url, params=params, headers=headers).status_code == 422


def test_timeline_rejects_corruption_outside_requested_page(tmp_path):
    path = tmp_path / "paper.health.sqlite3"
    seed(path)
    with TestClient(
        create_app(
            operator_token="operator",
            paper_db_path=tmp_path / "paper.sqlite3",
            health_watchdog_enabled=False,
        )
    ) as client:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE health_incidents SET incident_hash='damaged' WHERE incident_json LIKE '%fixture-0%'"
            )
        response = client.get(
            "/api/v1/paper/health/incidents/page?limit=1", headers={"X-Operator-Token": "operator"}
        )
        assert response.status_code == 503
        assert "fixture" not in response.text


def test_health_timeline_response_is_typed_and_schema_matches_runtime():
    app = create_app(operator_token="operator")
    response = app.openapi()["paths"]["/api/v1/paper/health/incidents/page"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]
    assert response == {"$ref": "#/components/schemas/HealthTimelinePage"}
    from ai_quant_os.health_timeline_contracts import HealthTimelinePage

    assert (
        json.loads(Path("docs/schemas/health-timeline-page.schema.json").read_text())
        == HealthTimelinePage.model_json_schema()
    )


def test_health_status_does_not_mix_different_observations():
    class Worker:
        observations = 0

        def status(self):
            self.observations += 1
            return {
                "schema_version": "1.0",
                "state": "MONITORING" if self.observations == 1 else "ERROR",
                "last_error": None
                if self.observations == 1
                else "WATCHDOG_VERIFICATION_OR_STORAGE_FAILURE",
            }

    app = FastAPI()
    install_health_routes(app, lambda: None, Worker(), True)
    with TestClient(app) as client:
        response = client.get("/api/v1/paper/health").json()
        assert response["state"] == "MONITORING"
        assert response["last_error"] is None

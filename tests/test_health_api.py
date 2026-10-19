import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.agent_registry import load_agent_catalog
from test_graph_runtime import context, open_runtime


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_health_disabled_commands_are_protected():
    with TestClient(create_app(operator_token="operator")) as client:
        assert client.get("/api/v1/paper/health").status_code == 401
        headers = {"X-Operator-Token": "operator"}
        assert client.get("/api/v1/paper/health", headers=headers).json()["state"] == "DISABLED"
        assert client.post("/api/v1/paper/health/scan", headers=headers).status_code == 503


@pytest.mark.parametrize(
    "options",
    [
        {"health_watchdog_enabled": True},
        {"health_watchdog_enabled": "true", "operator_token": "operator"},
    ],
)
def test_health_activation_requires_persistent_owner(tmp_path, options):
    with pytest.raises(ValueError):
        create_app(**options)


def test_application_detects_failed_agent_before_serving_and_keeps_quarantine_when_disabled(
    tmp_path,
):
    async def fail_run():
        registry = load_agent_catalog()
        evidence = await context(registry)

        async def broken(invocation):
            raise RuntimeError("injected failure")

        async with await open_runtime(
            tmp_path / "paper.agents.sqlite3", registry, broken
        ) as runtime:
            assert (await runtime.run("health-run", evidence)).status == "FAILED"

    asyncio.run(fail_run())
    options = dict(
        operator_token="operator",
        recovery_token="recovery",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    headers = {"X-Operator-Token": "operator"}
    with TestClient(create_app(**options, health_watchdog_enabled=True)) as client:
        health = client.get("/api/v1/paper/health", headers=headers).json()
        assert health["open_incidents"] == 1
        assert health["quarantined_versions"] == ["A02@0.1.0"]
        assert client.get("/api/v1/risk").json()["safety_state"] == "NO_NEW_RISK"
        assert (
            client.post(
                "/api/v1/paper/health/scan", json={"healthy": True}, headers=headers
            ).status_code
            == 422
        )
        assert client.post("/api/v1/paper/health/scan", headers=headers).status_code == 200
        items = client.get("/api/v1/paper/health/incidents", headers=headers).json()["incidents"]
        assert len(items) == 1
        incident_id = items[0]["incident_id"]
        evidence = client.get(
            f"/api/v1/paper/health/incidents/{incident_id}", headers=headers
        ).json()
        assert evidence["code"] == "AGENT_FAILED"
        assert evidence["quarantine"] is True
    with TestClient(create_app(**options, health_watchdog_enabled=False)) as client:
        health = client.get("/api/v1/paper/health", headers=headers).json()
        assert health["enabled"] is False
        assert health["quarantined_versions"] == ["A02@0.1.0"]
        response = client.post(
            "/api/v1/control/recover-paper",
            json={"reason": "attempt bypass"},
            headers={**headers, "X-Recovery-Token": "recovery", "Idempotency-Key": "bypass"},
        )
        assert response.status_code == 409


def test_corrupt_restored_journal_blocks_commands_and_halts_without_monitoring(tmp_path):
    import sqlite3

    options = dict(
        operator_token="operator", paper_db_path=tmp_path / "paper.sqlite3", clock=lambda: NOW
    )
    with TestClient(create_app(**options, health_watchdog_enabled=True)):
        pass
    with TestClient(create_app(**options, health_watchdog_enabled=False)) as client:
        with sqlite3.connect(tmp_path / "paper.health.sqlite3") as connection:
            connection.execute("UPDATE audit_head SET head_hash = 'corrupted'")
        response = client.post(
            "/api/v1/control/no-new-risk",
            json={"reason": "test command"},
            headers={"X-Operator-Token": "operator", "Idempotency-Key": "corrupt"},
        )
        assert response.status_code == 503
        assert client.get("/api/v1/risk").json()["safety_state"] == "HALT"


def test_quote_incident_close_route_requires_operator_and_fresh_quote(tmp_path):
    options = dict(
        operator_token="operator",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
        health_watchdog_enabled=True,
    )
    headers = {"X-Operator-Token": "operator"}
    with TestClient(create_app(**options)) as client:
        client.post(
            "/api/v1/paper/quotes",
            headers=headers,
            json={
                "instrument_id": "EURUSD",
                "bid": "1.1000",
                "ask": "1.1001",
                "observed_at": (NOW - timedelta(seconds=6)).isoformat(),
                "world_state_version": "old",
            },
        )
        client.post("/api/v1/paper/health/scan", headers=headers)
        incidents = client.get("/api/v1/paper/health/incidents", headers=headers).json()[
            "incidents"
        ]
        incident_id = incidents[0]["incident_id"]
        path = f"/api/v1/paper/health/incidents/{incident_id}/close"
        assert (
            client.post(
                path, json={"reason": "resolved"}, headers={"Idempotency-Key": "close-quote"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                path,
                json={"reason": "resolved"},
                headers={**headers, "Idempotency-Key": "close-quote"},
            ).status_code
            == 409
        )
        client.post(
            "/api/v1/paper/quotes",
            headers=headers,
            json={
                "instrument_id": "EURUSD",
                "bid": "1.1000",
                "ask": "1.1001",
                "observed_at": NOW.isoformat(),
                "world_state_version": "fresh",
            },
        )
        response = client.post(
            path,
            json={"reason": "trusted quote restored"},
            headers={**headers, "Idempotency-Key": "close-quote"},
        )
        assert response.status_code == 200
        assert client.get("/api/v1/paper/health", headers=headers).json()["open_incidents"] == 0
        item = client.get(f"/api/v1/paper/health/incidents/{incident_id}", headers=headers).json()
        assert item["status"] == "CLOSED"
        assert item["transitions"][0]["action"] == "CLOSED"


def test_persistent_api_lifespan_has_one_crash_released_owner(tmp_path):
    options = {
        "operator_token": "operator",
        "paper_db_path": tmp_path / "paper.sqlite3",
    }
    first = TestClient(create_app(**options))
    with first:
        competing = TestClient(create_app(**options))
        try:
            with pytest.raises(ValueError, match="already owned"):
                competing.__enter__()
        finally:
            competing.close()

    with TestClient(create_app(**options)) as successor:
        assert successor.get("/api/v1/risk").status_code == 200

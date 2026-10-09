import json
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.mt5_sync_service import ServiceHealth, publish_health
from test_mt5_account_sync import NOW

PATH = "/api/v1/brokers/demo-sync/health"
HEADERS = {"X-Operator-Token": "operator"}


def test_demo_health_unconfigured_is_protected_and_not_native_health(monkeypatch):
    monkeypatch.delenv("AI_QUANT_DEMO_SYNC_STATE", raising=False)
    with TestClient(create_app(operator_token="operator", clock=lambda: NOW)) as client:
        assert client.get(PATH).status_code == 401
        response = client.get(PATH, headers=HEADERS)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        value = response.json()
        assert value["configured"] is False and value["observation"]["state"] == "UNAVAILABLE"
        assert value["source_kind"] == "LOCAL_UNVERIFIED_HEARTBEAT"
        assert value["may_submit_orders"] is False
        assert client.post(PATH, headers=HEADERS).status_code == 405


def test_server_configured_health_returns_available_stale_and_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_QUANT_DEMO_SYNC_STATE", str(tmp_path))
    time = [NOW]
    record = ServiceHealth(
        run_id=uuid4(),
        state="STOPPED",
        ticks=1,
        published_at=NOW,
        valid_until=NOW + timedelta(seconds=30),
    )
    publish_health(tmp_path / "health.json", record)
    with TestClient(create_app(operator_token="operator", clock=lambda: time[0])) as client:
        value = client.get(PATH + "?state_dir=ignored-private-path", headers=HEADERS).json()
        assert value["configured"] is True and value["observation"]["state"] == "AVAILABLE"
        assert value["observation"]["record"] == record.model_dump(mode="json")
        time[0] += timedelta(seconds=30)
        assert client.get(PATH, headers=HEADERS).json()["observation"]["state"] == "STALE"
        (tmp_path / "health.json").write_text("private malformed payload")
        response = client.get(PATH, headers=HEADERS)
        assert response.json()["observation"]["state"] == "UNAVAILABLE"
        assert "private" not in response.text and str(tmp_path) not in response.text


def test_authentication_runs_before_reading_configured_file(tmp_path, monkeypatch):
    import ai_quant_os.mt5_sync_health_api as api

    calls = []
    monkeypatch.setattr(api, "read_health", lambda *a, **k: calls.append(a))
    with TestClient(
        create_app(operator_token="operator", demo_sync_state_dir=tmp_path, clock=lambda: NOW)
    ) as client:
        assert client.get(PATH).status_code == 401
        assert client.get(PATH, headers={"X-Operator-Token": "wrong"}).status_code == 401
    assert calls == []


def test_exported_demo_health_contract_matches_runtime():
    from pathlib import Path
    from ai_quant_os.mt5_sync_health_api import DemoSyncHealthResponse

    path = Path(__file__).resolve().parents[1] / "docs/schemas/demo-sync-health-api.schema.json"
    assert (
        json.loads(path.read_text(encoding="utf-8")) == DemoSyncHealthResponse.model_json_schema()
    )


def test_revoked_owner_session_cannot_read_demo_health(tmp_path, monkeypatch):
    import ai_quant_os.mt5_sync_health_api as api

    reads = []
    original = api.read_health

    def observed(*args, **kwargs):
        reads.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "read_health", observed)
    with TestClient(
        create_app(operator_token="operator", demo_sync_state_dir=tmp_path, clock=lambda: NOW)
    ) as client:
        login = client.post("/api/v1/operator/sessions", headers=HEADERS)
        assert login.status_code == 201
        headers = {"X-Operator-Session": login.json()["session_token"]}
        assert client.get(PATH, headers=headers).status_code == 200
        assert client.delete("/api/v1/operator/session", headers=headers).status_code == 200
        assert client.get(PATH, headers=headers).status_code == 401
    assert len(reads) == 1


def test_local_launcher_configures_health_path_without_forwarding_password(tmp_path):
    from ai_quant_os.local_runtime import LocalSettings
    from pathlib import Path

    backend, frontend = LocalSettings(Path.cwd(), tmp_path).environments(
        {
            "AI_QUANT_DEMO_SYNC_STATE": "private-other-state",
            "AI_QUANT_MT5_PASSWORD": "fixture-private-password",
        }
    )
    assert backend["AI_QUANT_DEMO_SYNC_STATE"] == str(tmp_path.resolve() / "demo-sync")
    assert "AI_QUANT_MT5_PASSWORD" not in backend and "AI_QUANT_MT5_PASSWORD" not in frontend
    assert "AI_QUANT_DEMO_SYNC_STATE" not in frontend

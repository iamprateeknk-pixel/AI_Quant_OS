import sqlite3
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine
from ai_quant_os.world_state_store import WorldStateStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"X-Operator-Token": "fixture-token"}


def snapshot(now=NOW):
    return WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=now)


def test_immutable_snapshot_survives_restart_and_same_version_is_idempotent(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = WorldStateStore(path)
    original = snapshot()
    assert store.put(original) is True
    assert store.put(original) is False
    assert WorldStateStore(path).get(original.world_state_version) == original
    assert store.get("world:" + "0" * 64) is None
    with pytest.raises(ValueError, match="conflict"):
        store.put(replace(original, status="READY"))


def test_corruption_is_rejected_on_read_and_idempotent_write(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = WorldStateStore(path)
    original = snapshot()
    store.put(original)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE world_state_snapshots SET snapshot_json = '{}' ")
    with pytest.raises(ValueError, match="integrity"):
        store.get(original.world_state_version)
    with pytest.raises(ValueError, match="conflict"):
        store.put(original)


def test_concurrent_store_instances_enforce_capacity_without_losing_existing_rows(tmp_path):
    path = tmp_path / "paper.sqlite3"
    stores = [WorldStateStore(path, max_snapshots=1) for _ in range(8)]

    def publish(index):
        try:
            return stores[index].put(snapshot(NOW + timedelta(seconds=index)))
        except ValueError as error:
            assert "capacity" in str(error)
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(publish, range(8))) == 1
    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT world_state_version FROM world_state_snapshots"
        ).fetchone()[0]
    saved = stores[0].get(version)
    assert saved is not None
    assert stores[0].put(saved) is False


@pytest.mark.parametrize(
    "change",
    [
        {"as_of": NOW.replace(tzinfo=None)},
        {"valid_until": NOW - timedelta(seconds=1)},
        {"schema_version": "2.0"},
        {"source": "LIVE"},
        {"world_state_version": "arbitrary"},
    ],
)
def test_invalid_snapshot_contract_is_rejected(change):
    with pytest.raises(ValueError):
        WorldStateStore().put(replace(snapshot(), **change))


def test_api_publication_is_authorized_and_historical_data_never_restores_fresh_quotes(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="fixture-token", paper_db_path=path, clock=lambda: NOW)
    )
    url = "/api/v1/paper/world-state/publish"
    assert client.post(url).status_code == 401
    assert client.post(url, headers=HEADERS, json={"source": "LIVE"}).status_code == 422
    published = client.post(url, headers=HEADERS)
    assert published.status_code == 200
    version = published.json()["snapshot"]["world_state_version"]
    assert published.json()["archival_only"] is True
    assert client.post(url, headers=HEADERS).json()["inserted"] is False
    restarted = TestClient(
        create_app(
            operator_token="fixture-token",
            paper_db_path=path,
            clock=lambda: NOW + timedelta(seconds=6),
        )
    )
    historical = restarted.get(f"/api/v1/world-state/archive/{version}")
    assert historical.status_code == 200
    assert historical.json()["current"] is False
    assert historical.json()["snapshot"] == published.json()["snapshot"]
    assert restarted.get("/api/v1/world-state").json()["status"] == "EMPTY"
    assert restarted.get("/api/v1/orders").json()["orders"] == []
    assert restarted.get("/api/v1/world-state/archive/world:" + "0" * 64).status_code == 404


def test_classification_archives_input_and_links_audit_after_restart(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="fixture-token", paper_db_path=path, clock=lambda: NOW)
    )
    decision = client.post(
        "/api/v1/paper/decisions/classify",
        headers=HEADERS,
        json={"instrument_id": "EURUSD", "task_id": "fixture"},
    ).json()
    version = decision["world_state_version"]
    archived = WorldStateStore(path).get(version)
    assert archived is not None
    assert archived.as_of == NOW
    event = client.get("/api/v1/audit").json()["events"][-1]
    assert event["snapshot_ref"] == version


def test_storage_failure_prevents_provider_dispatch(monkeypatch):
    calls = []

    class Provider:
        provider_id = "fixture"
        provider_version = "1"

        async def classify(self, state, instrument):
            calls.append(state)
            raise AssertionError("Provider must not run")

    def unavailable(self, state):
        raise sqlite3.OperationalError("private-storage-details")

    monkeypatch.setattr(WorldStateStore, "put", unavailable)
    client = TestClient(
        create_app(operator_token="fixture-token", fast_classifier=Provider(), clock=lambda: NOW)
    )
    result = client.post(
        "/api/v1/paper/decisions/classify",
        headers=HEADERS,
        json={"instrument_id": "EURUSD", "task_id": "fixture"},
    )
    assert result.status_code == 503
    assert "private-storage-details" not in result.text
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"world_state_version": "world:" + "f" * 64},
        {"source": "LIVE"},
        {"unexpected": "field"},
    ],
)
def test_rehashed_invalid_identity_contract_or_noncanonical_fields_are_rejected(tmp_path, change):
    path = tmp_path / "paper.sqlite3"
    store = WorldStateStore(path)
    original = snapshot()
    store.put(original)
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT snapshot_json FROM world_state_snapshots").fetchone()[0]
        payload = {**json.loads(raw), **change}
        edited = json.dumps(payload, separators=(",", ":"))
        connection.execute(
            "UPDATE world_state_snapshots SET snapshot_json = ?, content_hash = ?",
            (edited, hashlib.sha256(edited.encode()).hexdigest()),
        )
    with pytest.raises(ValueError, match="integrity"):
        store.get(original.world_state_version)
    client = TestClient(
        create_app(operator_token="fixture-token", paper_db_path=path, clock=lambda: NOW)
    )
    assert (
        client.get(f"/api/v1/world-state/archive/{original.world_state_version}").status_code == 503
    )


def test_concurrent_same_version_inserts_once(tmp_path):
    path = tmp_path / "paper.sqlite3"
    stores = [WorldStateStore(path) for _ in range(8)]
    original = snapshot()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda store: store.put(original), stores))
    assert sum(results) == 1
    assert all(store.get(original.world_state_version) == original for store in stores)

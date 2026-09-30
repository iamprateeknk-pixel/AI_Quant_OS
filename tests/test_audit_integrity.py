import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.paper_state_store import PaperStateStore


def event(reason):
    return {"action": "TEST", "intent_id": "", "reason": reason}


def test_integrity_survives_restart_and_duplicate_events_do_not_extend_chain(tmp_path):
    path = tmp_path / "state.db"
    store = PaperStateStore(path)
    store.record_event("first", event("one"))
    store.record_event("first", event("one"))
    store.record_event("second", event("two"))
    report = store.audit_integrity()
    assert report.valid is True
    assert report.event_count == 2
    assert len(report.head_hash) == 64
    assert PaperStateStore(path).audit_integrity() == report
    with pytest.raises(ValueError, match="different"):
        store.record_event("first", event("changed"))


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE audit_events SET event_json = '{}' WHERE event_key = 'first'",
        "DELETE FROM audit_events WHERE event_key = 'first'",
        "DELETE FROM audit_events WHERE event_key = 'second'",
        "UPDATE audit_chain SET sequence = 9 WHERE sequence = 1",
        "DELETE FROM audit_chain WHERE sequence = 2",
        "UPDATE audit_head SET head_hash = 'changed'",
        "INSERT INTO audit_events VALUES ('extra', '{}')",
    ],
)
def test_tampering_is_reported_and_rejected_on_read_write_and_restart(tmp_path, tamper):
    path = tmp_path / "state.db"
    store = PaperStateStore(path)
    store.record_event("first", event("one"))
    store.record_event("second", event("two"))
    with sqlite3.connect(path) as connection:
        connection.execute(tamper)
    assert store.audit_integrity().valid is False
    with pytest.raises(ValueError, match="integrity"):
        store.audit_events()
    with pytest.raises(ValueError, match="integrity"):
        store.record_event("third", event("three"))
    with pytest.raises(ValueError, match="integrity"):
        PaperStateStore(path)


def test_legacy_audit_history_is_baselined_once_and_disclosed(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO audit_events VALUES (?, ?)", ("legacy", json.dumps(event("old")))
        )
    store = PaperStateStore(path)
    assert store.audit_events() == [event("old")]
    assert store.audit_integrity().legacy_event_count == 1
    store.record_event("new", event("new"))
    report = PaperStateStore(path).audit_integrity()
    assert report.event_count == 2
    assert report.legacy_event_count == 1


def test_concurrent_connections_produce_one_contiguous_chain(tmp_path):
    path = tmp_path / "shared.db"
    stores = [PaperStateStore(path), PaperStateStore(path)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(
            workers.map(
                lambda index: stores[index % 2].record_event(str(index), event(str(index))),
                range(20),
            )
        )
    assert stores[0].audit_integrity().valid is True
    assert stores[0].audit_integrity().event_count == 20


def test_integrity_report_is_available_without_command_credentials():
    client = TestClient(create_app())
    response = client.get("/api/v1/audit/integrity")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "1.0"
    assert response.json()["valid"] is True
    assert response.json()["event_count"] == 0


def test_startup_backfill_preserves_original_history():
    store = PaperStateStore()
    store.record_event("existing", event("original"))
    head = store.audit_integrity()
    store.backfill_event("existing", event("reconstructed"))
    assert store.audit_integrity() == head
    assert store.audit_events() == [event("original")]
    store.backfill_event("missing", event("reconstructed"))
    assert store.audit_integrity().event_count == 2


def test_running_api_reports_corruption_and_rejects_commands(tmp_path):
    path = tmp_path / "state.db"
    store = PaperStateStore(path)
    store.record_event("first", event("one"))
    client = TestClient(create_app(operator_token="fixture-token", paper_db_path=path))
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM audit_events")
    assert client.get("/api/v1/audit/integrity").json()["valid"] is False
    assert client.get("/api/v1/audit").status_code == 503
    response = client.post(
        "/api/v1/paper/quotes",
        headers={"X-Operator-Token": "fixture-token"},
        json={
            "instrument_id": "EURUSD",
            "bid": "1.1",
            "ask": "1.1001",
            "observed_at": "2026-01-01T00:00:00Z",
            "world_state_version": "fixture",
        },
    )
    assert response.status_code == 503


def test_failed_chain_head_write_rolls_back_event_and_safety_state(tmp_path):
    from ai_quant_os.risk import SafetyState

    path = tmp_path / "atomic.db"
    store = PaperStateStore(path)
    before = store.audit_integrity()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_head BEFORE UPDATE ON audit_head BEGIN SELECT RAISE(ABORT, 'head unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="head unavailable"):
        store.record_control("halt", SafetyState.HALT, "test stop", {"receipt_id": "receipt"})
    assert store.safety_state() == SafetyState.NORMAL
    assert store.control_receipt("halt") is None
    assert store.audit_integrity() == before
    assert store.audit_events() == []

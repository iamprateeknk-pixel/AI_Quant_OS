import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.health_store import HealthStore


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def observation(subject="EURUSD"):
    return HealthIncident("QUOTE_STALE", subject, '{"source":"fixture"}', NOW)


def test_concurrent_duplicates_retain_first_observation_and_capacity(tmp_path):
    path = tmp_path / "health.sqlite3"
    stores = [HealthStore(path, capacity=1), HealthStore(path, capacity=1)]
    first = stores[0].record(observation())
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda store: store.record(replace(first, detected_at=NOW + timedelta(seconds=1))),
                stores,
            )
        )
    assert receipts == [first, first]
    assert len(stores[1].incidents()) == 1
    with pytest.raises(ValueError, match="capacity"):
        stores[1].record(observation("GBPUSD"))
    assert stores[0].integrity().valid
    assert stores[0].incidents() == (first,)


def test_rehashed_first_observation_tamper_still_fails_audit_binding(tmp_path):
    path = tmp_path / "health.sqlite3"
    store = HealthStore(path)
    store.record(observation())
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT incident_json FROM health_incidents").fetchone()[0]
        payload = json.loads(raw)
        payload["detected_at"] = (NOW + timedelta(seconds=1)).isoformat()
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        connection.execute(
            "UPDATE health_incidents SET incident_json = ?, incident_hash = ?",
            (raw, sha256(raw.encode()).hexdigest()),
        )
    with pytest.raises(ValueError, match="binding"):
        store.incidents()


def test_deleted_incident_cannot_hide_its_journal_head(tmp_path):
    path = tmp_path / "health.sqlite3"
    store = HealthStore(path)
    store.record(observation())
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM health_incidents")
    with pytest.raises(ValueError, match="count"):
        HealthStore(path)


def test_oversized_serialized_evidence_is_refused_without_poisoning_journal(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    evidence = json.dumps({"note": "\\" * 8180}, separators=(",", ":"))
    incident = HealthIncident("QUOTE_STALE", "EURUSD", evidence, NOW)
    with pytest.raises(ValueError, match="bound"):
        store.record(incident)
    assert store.incidents() == ()
    assert store.integrity().valid


def test_quote_incident_can_be_closed_and_reopens_on_reobservation(tmp_path):
    path = tmp_path / "health.sqlite3"
    store = HealthStore(path)
    incident = store.record(observation())

    closed = store.close_incident(
        incident.incident_id,
        closed_at=NOW + timedelta(seconds=1),
        command_id="operator-close-1",
        reason="trusted_quote_recovered",
    )
    assert closed["action"] == "CLOSED"
    assert store.open_incidents() == ()
    assert store.incidents() == (incident,)
    assert store.integrity().valid

    # A subsequent identical source observation must reopen the incident.
    assert store.record(replace(incident, detected_at=NOW + timedelta(seconds=2))) == incident
    assert store.open_incidents() == (incident,)
    assert [event["action"] for event in store.transitions(incident.incident_id)] == [
        "CLOSED",
        "REOPENED",
    ]
    assert store.integrity().valid


def test_only_quote_incidents_can_be_closed_and_close_command_is_idempotent(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    incident = store.record(observation())
    kwargs = {
        "closed_at": NOW + timedelta(seconds=1),
        "command_id": "operator-close-1",
        "reason": "trusted_quote_recovered",
    }
    first = store.close_incident(incident.incident_id, **kwargs)
    assert store.close_incident(incident.incident_id, **kwargs) == first
    assert len(store.transitions(incident.incident_id)) == 1

    agent = HealthIncident(
        "AGENT_FAILED",
        "run-1",
        '{"status":"FAILED"}',
        NOW,
        agent_id="A02",
        agent_version="0.2.0",
        manifest_ref="sha256:fixture",
    )
    store.record(agent)
    with pytest.raises(ValueError, match="quote"):
        store.close_incident(
            agent.incident_id,
            closed_at=NOW + timedelta(seconds=1),
            command_id="operator-close-2",
            reason="trusted_quote_recovered",
        )
    assert agent in store.open_incidents()


def test_close_state_and_transition_audit_survive_restart(tmp_path):
    path = tmp_path / "health.sqlite3"
    store = HealthStore(path)
    incident = store.record(observation())
    store.close_incident(
        incident.incident_id,
        closed_at=NOW + timedelta(seconds=1),
        command_id="operator-close-1",
        reason="trusted_quote_recovered",
    )
    reopened = HealthStore(path)
    assert reopened.open_incidents() == ()
    assert reopened.transitions(incident.incident_id)[0]["action"] == "CLOSED"
    assert reopened.integrity().valid


def test_closure_command_cannot_be_reused_for_another_incident(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    first = store.record(observation("EURUSD"))
    second = store.record(observation("GBPUSD"))
    store.close_incident(
        first.incident_id,
        closed_at=NOW + timedelta(seconds=1),
        command_id="operator-close-1",
        reason="trusted_quote_recovered",
    )
    with pytest.raises(ValueError, match="already used"):
        store.close_incident(
            second.incident_id,
            closed_at=NOW + timedelta(seconds=1),
            command_id="operator-close-1",
            reason="trusted_quote_recovered",
        )


def test_open_incident_read_still_checks_closed_record_integrity(tmp_path):
    path = tmp_path / "health.sqlite3"
    store = HealthStore(path)
    incident = store.record(observation())
    store.close_incident(
        incident.incident_id,
        closed_at=NOW + timedelta(seconds=1),
        command_id="operator-close-1",
        reason="trusted_quote_recovered",
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE health_incidents SET incident_json = '{}' WHERE incident_id = ?",
            (incident.incident_id,),
        )
    with pytest.raises(ValueError, match="integrity"):
        store.open_incidents()

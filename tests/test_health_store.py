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

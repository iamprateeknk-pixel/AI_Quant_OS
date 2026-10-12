"""Original observed episodes and authenticated review receipts survive owned journal reopen."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from ai_quant_os.monitoring_alerts import MonitoringObservation

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def journal():
    return importlib.import_module("ai_quant_os.monitoring_journal").MonitoringJournal


def observation(state="PENDING", offset=0):
    value = json.loads(
        (
            Path(__file__).resolve().parents[1] / "apps/web/tests/fixtures/monitoring-alerts.json"
        ).read_text()
    )
    value["observed_at"] = (NOW + timedelta(seconds=offset)).isoformat()
    for i, rule in enumerate(value["rules"]):
        rule["evaluated_at"] = value["observed_at"]
        rule["state"] = state if i == 0 else "INACTIVE"
        rule["active_since"] = (
            (NOW - timedelta(minutes=3)).isoformat() if i == 0 and state != "INACTIVE" else None
        )
    return MonitoringObservation.model_validate(value)


def test_original_episode_progression_gaps_and_review_do_not_imply_current_recovery(tmp_path):
    path = tmp_path / "monitoring.sqlite3"
    store = journal()(path)
    pending = store.record(observation())
    episode = pending.episodes[0]
    assert episode.state == "PENDING" and episode.acknowledgement is None
    assert store.record(observation(offset=15)).head_hash == pending.head_hash
    firing = store.record(observation("FIRING", offset=30))
    assert firing.episodes[0].episode_id == episode.episode_id
    actor, action = uuid4(), uuid4()
    receipt = store.acknowledge(
        episode.episode_id,
        action_id=action,
        actor_session_id=actor,
        expected_head=firing.head_hash,
        now=NOW + timedelta(seconds=31),
        authorize=lambda: actor,
    )
    assert receipt.committed is True and receipt.control_authorized is False
    assert store.history().episodes[0].state == "FIRING"
    unavailable = MonitoringObservation(
        observed_at=NOW + timedelta(seconds=45), state="UNAVAILABLE"
    )
    gap = store.record(unavailable)
    assert gap.episodes[0].state == "NOT_CURRENTLY_OBSERVED"
    assert gap.episodes[0].acknowledgement == receipt
    inactive = store.record(observation("INACTIVE", offset=60))
    assert inactive.episodes[0].state == "OBSERVED_INACTIVE"
    assert inactive.episodes[0].acknowledgement == receipt
    store.close()
    reopened = journal()(path)
    assert reopened.history() == inactive
    reopened.close()


def test_cross_connection_original_ack_replay_conflicts_and_head_change(tmp_path):
    path = tmp_path / "monitoring.sqlite3"
    a = journal()(path)
    b = journal()(path)
    history = a.record(observation())
    episode = history.episodes[0].episode_id
    actor, action = uuid4(), uuid4()

    def acknowledge(store):
        return store.acknowledge(
            episode,
            action_id=action,
            actor_session_id=actor,
            expected_head=history.head_hash,
            now=NOW + timedelta(seconds=1),
            authorize=lambda: actor,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(acknowledge, (a, b)))
    assert first == second
    assert (
        b.acknowledge(
            episode,
            action_id=action,
            actor_session_id=actor,
            expected_head=history.head_hash,
            now=NOW + timedelta(seconds=2),
            authorize=lambda: actor,
        )
        == first
    )
    with pytest.raises(ValueError):
        b.acknowledge(
            episode,
            action_id=action,
            actor_session_id=uuid4(),
            expected_head=history.head_hash,
            now=NOW,
            authorize=lambda: actor,
        )
    with pytest.raises(ValueError):
        b.acknowledge(
            episode,
            action_id=uuid4(),
            actor_session_id=actor,
            expected_head=history.head_hash,
            now=NOW,
            authorize=lambda: actor,
        )
    a.close()
    b.close()


def test_admission_revocation_rolls_back_original_ack_before_commit(tmp_path):
    store = journal()(tmp_path / "monitoring.sqlite3")
    history = store.record(observation())
    actor = uuid4()
    calls = []

    def authorize():
        calls.append(1)
        if len(calls) > 1:
            raise PermissionError("fixture revoked")
        return actor

    with pytest.raises(PermissionError):
        store.acknowledge(
            history.episodes[0].episode_id,
            action_id=uuid4(),
            actor_session_id=actor,
            expected_head=history.head_hash,
            now=NOW,
            authorize=authorize,
        )
    assert store.history() == history
    store.close()


@pytest.mark.parametrize("change", ["event", "head", "metadata"])
def test_changed_existing_journal_withholds_without_reinitializing(tmp_path, change):
    path = tmp_path / "monitoring.sqlite3"
    store = journal()(path)
    store.record(observation())
    store.close()
    with sqlite3.connect(path) as db:
        if change == "event":
            db.execute("UPDATE audit_events SET event_json='{}'")
        if change == "head":
            db.execute("DELETE FROM audit_head")
        if change == "metadata":
            db.execute("DROP TABLE monitoring_metadata")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        journal()(path)
    assert path.read_bytes() == before


def test_bounded_capacity_preserves_earlier_episode_and_rejects_clock_regression(tmp_path):
    path = tmp_path / "monitoring.sqlite3"
    store = journal()(path, capacity=1)
    history = store.record(observation())
    assert store.record(observation(offset=15)) == history
    with pytest.raises(ValueError):
        store.record(observation("FIRING", offset=30))
    assert store.history() == history
    with pytest.raises(ValueError):
        store.record(observation(offset=-1))
    assert store.history() == history
    store.close()

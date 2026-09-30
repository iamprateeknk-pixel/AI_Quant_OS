import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.agent_runs import AgentRun, verify_run_snapshot
from ai_quant_os.api import create_app
from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.decision_gateway import DecisionGateway
from ai_quant_os.deep_review import DeepReviewDispatcher
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.world_state import WorldStateEngine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def run():
    async def invoke():
        world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
        decision = await DecisionGateway(clock=lambda: NOW).classify(
            world, instrument_id="EURUSD", task_id="fixture"
        )
        review = await DeepReviewDispatcher(clock=lambda: NOW).dispatch(world, decision)
        return AgentRun(
            schema_version="1.0",
            run_id=decision.trace_id,
            snapshot_ref=world.world_state_version,
            decision=decision,
            deep_review=review,
            recorded_at=NOW,
        )

    return asyncio.run(invoke())


def test_run_and_audit_survive_restart_and_exact_retry_is_idempotent(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    item = run()
    assert store.record_agent_run(item) is True
    assert store.record_agent_run(item) is False
    restarted = PaperStateStore(path)
    assert restarted.agent_run(item.run_id) == item
    assert restarted.agent_run("missing") is None
    event = restarted.audit_events()[-1]
    assert event["run_id"] == item.run_id
    assert event["deep_review_trace_id"] == item.deep_review.trace_id
    assert restarted.audit_integrity().valid


def test_audit_append_failure_rolls_back_run(monkeypatch, tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    item = run()

    def fail(self, key, event, **kwargs):
        raise ValueError("fixture audit failure")

    monkeypatch.setattr(AuditChain, "append", fail)
    with pytest.raises(ValueError, match="fixture audit failure"):
        store.record_agent_run(item)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
    assert store.audit_events() == []


def test_concurrent_exact_run_commits_one_result_and_one_audit_event(tmp_path):
    path = tmp_path / "paper.sqlite3"
    stores = [PaperStateStore(path) for _ in range(8)]
    item = run()
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(lambda store: store.record_agent_run(item), stores)) == 1
    assert len(stores[0].audit_events()) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "wrong"),
        ("snapshot_ref", "wrong"),
    ],
)
def test_bundle_mismatch_is_rejected(field, value):
    raw = run().model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValueError):
        AgentRun.model_validate(raw)


def test_cross_task_review_and_removed_escalation_are_rejected():
    raw = run().model_dump(mode="json")
    raw["deep_review"]["task_id"] = "another-task"
    with pytest.raises(ValueError):
        AgentRun.model_validate(raw)
    raw = run().model_dump(mode="json")
    raw["decision"]["requires_escalation"] = False
    with pytest.raises(ValueError):
        AgentRun.model_validate(raw)


def test_edited_run_is_rejected_even_when_its_local_hash_is_updated(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    item = run()
    store.record_agent_run(item)
    with sqlite3.connect(path) as connection:
        raw = json.loads(connection.execute("SELECT run_json FROM agent_runs").fetchone()[0])
        raw["decision"]["output"]["fallback_reasons"] = ["edited"]
        edited = AgentRun.model_validate(raw).model_dump_json()
        connection.execute(
            "UPDATE agent_runs SET run_json = ?, content_hash = ?",
            (edited, hashlib.sha256(edited.encode()).hexdigest()),
        )
    with pytest.raises(ValueError, match="integrity"):
        store.agent_run(item.run_id)


def test_api_retrieves_linked_run_after_restart_and_keeps_expired_output_archival(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="fixture-token", paper_db_path=path, clock=lambda: NOW)
    )
    response = client.post(
        "/api/v1/paper/decisions/classify",
        headers={"X-Operator-Token": "fixture-token"},
        json={"instrument_id": "EURUSD", "task_id": "fixture"},
    )
    assert response.status_code == 200
    decision = response.json()
    restarted = TestClient(
        create_app(
            operator_token="fixture-token",
            paper_db_path=path,
            clock=lambda: NOW + timedelta(seconds=6),
        )
    )
    saved = restarted.get(f"/api/v1/agents/runs/{decision['trace_id']}")
    assert saved.status_code == 200
    body = saved.json()
    assert body["archival_only"] is True
    assert body["current"] is False
    assert body["run"]["decision"]["trace_id"] == decision["trace_id"]
    assert body["run"]["deep_review"] == decision["deep_review"]
    assert restarted.get("/api/v1/orders").json()["orders"] == []
    assert restarted.get("/api/v1/agents/runs/missing").status_code == 404


def test_changed_run_conflicts_without_changing_existing_audit(tmp_path):
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    item = run()
    store.record_agent_run(item)
    raw = item.model_dump(mode="json")
    raw["decision"]["task_id"] = raw["deep_review"]["task_id"] = "changed"
    with pytest.raises(ValueError, match="conflict"):
        store.record_agent_run(AgentRun.model_validate(raw))
    assert store.agent_run(item.run_id) == item
    assert len(store.audit_events()) == 1


def test_snapshot_evidence_and_expiry_are_checked_without_requiring_current_time():
    item = run()
    snapshot = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    verify_run_snapshot(item, snapshot)
    raw = item.model_dump(mode="json")
    raw["decision"]["evidence_refs"] = ["invented"]
    with pytest.raises(ValueError, match="evidence"):
        verify_run_snapshot(AgentRun.model_validate(raw), snapshot)


def test_concurrent_capacity_and_duplicate_review_trace_roll_back_cleanly(monkeypatch, tmp_path):
    import ai_quant_os.agent_run_repository as repository

    monkeypatch.setattr(repository, "_MAX_RUNS", 1)
    path = tmp_path / "paper.sqlite3"
    stores = [PaperStateStore(path) for _ in range(4)]
    items = [run() for _ in stores]

    def insert(pair):
        store, item = pair
        try:
            return store.record_agent_run(item)
        except ValueError as error:
            assert "capacity" in str(error)
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(insert, zip(stores, items))) == 1
    assert len(stores[0].audit_events()) == 1
    assert stores[0].audit_integrity().valid
    monkeypatch.setattr(repository, "_MAX_RUNS", 10000)
    original = next(item for item in items if stores[0].agent_run(item.run_id))
    changed = run().model_dump(mode="json")
    changed["deep_review"]["trace_id"] = original.deep_review.trace_id
    with pytest.raises(sqlite3.IntegrityError):
        stores[0].record_agent_run(AgentRun.model_validate(changed))
    assert len(stores[0].audit_events()) == 1


def test_missing_snapshot_or_corrupt_result_yields_sanitized_api_failure(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="fixture-token", paper_db_path=path, clock=lambda: NOW)
    )
    response = client.post(
        "/api/v1/paper/decisions/classify",
        headers={"X-Operator-Token": "fixture-token"},
        json={"instrument_id": "EURUSD", "task_id": "fixture"},
    )
    run_id = response.json()["trace_id"]
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM world_state_snapshots")
    assert client.get(f"/api/v1/agents/runs/{run_id}").status_code == 503
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE agent_runs SET run_json = 'private-corrupt-data'")
    result = client.get(f"/api/v1/agents/runs/{run_id}")
    assert result.status_code == 503
    assert "private-corrupt-data" not in result.text

import asyncio
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.agent_context import ContextCompiler, require_context_for_agent
from ai_quant_os.context_checkpoints import ContextCheckpoint
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.paper_state_store import PaperStateStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"X-Operator-Token": "fixture-token"}


def checkpoint(task="fixture"):
    registry = load_agent_catalog()
    context = asyncio.run(
        ContextCompiler(registry, {}, clock=lambda: NOW).compile(
            "A02", "0.1.0", task_id=task, scope="PAPER"
        )
    )
    return ContextCheckpoint(
        schema_version="1.0", manifest=registry.get("A02", "0.1.0"), context=context
    )


def test_checkpoint_and_exact_manifest_survive_restart_without_reactivating_expired_context(
    tmp_path,
):
    path = tmp_path / "paper.sqlite3"
    item = checkpoint()
    store = PaperStateStore(path)
    assert store.record_context_checkpoint(item) is True
    assert store.record_context_checkpoint(item) is False
    restarted = PaperStateStore(path)
    saved = restarted.context_checkpoint(item.context.context_ref)
    assert saved == item
    assert saved.manifest.implementation_status == "PLANNED"
    assert restarted.context_checkpoint("missing") is None
    with pytest.raises(ValueError, match="expired"):
        require_context_for_agent(
            saved.context,
            registry=load_agent_catalog(),
            agent_id="A02",
            agent_version="0.1.0",
            task_id="fixture",
            scope="PAPER",
            now=NOW + timedelta(seconds=6),
        )
    assert len(restarted.audit_events()) == 1
    assert restarted.audit_integrity().valid


def test_atomic_audit_failure_rolls_back_full_checkpoint(monkeypatch, tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)

    def fail(self, key, event, **kwargs):
        raise ValueError("fixture append failure")

    monkeypatch.setattr(AuditChain, "append", fail)
    with pytest.raises(ValueError, match="fixture append failure"):
        store.record_context_checkpoint(checkpoint())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM context_checkpoints").fetchone()[0] == 0
    assert store.audit_events() == []


def test_concurrent_duplicate_checkpoint_commits_once(tmp_path):
    path = tmp_path / "paper.sqlite3"
    stores = [PaperStateStore(path) for _ in range(6)]
    item = checkpoint()
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(lambda store: store.record_context_checkpoint(item), stores)) == 1
    assert len(stores[0].audit_events()) == 1


def test_manifest_mismatch_is_rejected():
    raw = checkpoint().model_dump(mode="json")
    raw["manifest"]["mission"] = "Edited after compilation"
    with pytest.raises(ValueError, match="manifest"):
        ContextCheckpoint.model_validate(raw)


def test_rehashed_noncanonical_checkpoint_is_rejected(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    item = checkpoint()
    store.record_context_checkpoint(item)
    with sqlite3.connect(path) as connection:
        edited = item.model_dump_json() + " "
        connection.execute(
            "UPDATE context_checkpoints SET checkpoint_json = ?, content_hash = ?",
            (edited, hashlib.sha256(edited.encode()).hexdigest()),
        )
    with pytest.raises(ValueError, match="integrity"):
        store.context_checkpoint(item.context.context_ref)


def test_api_checkpoint_archive_requires_operator_and_keeps_original_validity(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, paper_db_path=path)
    )
    response = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={"agent_id": "A02", "agent_version": "0.1.0", "task_id": "fixture"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["checkpoint_ref"] == body["context_ref"]
    url = f"/api/v1/agents/contexts/{body['checkpoint_ref']}"
    restarted = TestClient(
        create_app(
            operator_token="fixture-token",
            clock=lambda: NOW + timedelta(seconds=6),
            paper_db_path=path,
        )
    )
    assert restarted.get(url).status_code == 401
    saved = restarted.get(url, headers=HEADERS)
    assert saved.status_code == 200
    assert saved.json()["current"] is False
    assert saved.json()["archival_only"] is True
    assert saved.json()["checkpoint"]["context"]["valid_until"] == body["valid_until"]
    assert restarted.get("/api/v1/agents/contexts/missing", headers=HEADERS).status_code == 404
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE context_checkpoints SET checkpoint_json = 'private-invalid-evidence'"
        )
    failed = restarted.get(url, headers=HEADERS)
    assert failed.status_code == 503
    assert "private-invalid-evidence" not in failed.text


def test_valid_audit_chain_with_wrong_checkpoint_binding_is_rejected(monkeypatch, tmp_path):
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    original = AuditChain.append

    def wrong_binding(self, key, event, **kwargs):
        return original(self, key, {**event, "checkpoint_hash": "0" * 64}, **kwargs)

    monkeypatch.setattr(AuditChain, "append", wrong_binding)
    item = checkpoint()
    store.record_context_checkpoint(item)
    assert store.audit_integrity().valid
    with pytest.raises(ValueError, match="integrity"):
        store.context_checkpoint(item.context.context_ref)


def test_concurrent_capacity_and_idempotence_at_capacity(monkeypatch, tmp_path):
    import ai_quant_os.context_checkpoint_repository as repository

    monkeypatch.setattr(repository, "_MAX_CHECKPOINTS", 1)
    path = tmp_path / "paper.sqlite3"
    stores = [PaperStateStore(path) for _ in range(4)]
    items = [checkpoint(f"task-{index}") for index in range(4)]

    def insert(pair):
        store, item = pair
        try:
            return store.record_context_checkpoint(item)
        except ValueError as error:
            assert "capacity" in str(error)
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(insert, zip(stores, items))) == 1
    assert len(stores[0].audit_events()) == 1
    saved = next(item for item in items if stores[0].context_checkpoint(item.context.context_ref))
    assert stores[0].record_context_checkpoint(saved) is False
    assert stores[0].audit_integrity().valid

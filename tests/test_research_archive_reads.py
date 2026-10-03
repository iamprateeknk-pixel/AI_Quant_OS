import asyncio
from datetime import timedelta
import sqlite3

import pytest

from ai_quant_os.world_state_store import WorldStateStore
from ai_quant_os.graph_ledger import GraphLedger
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.context_checkpoints import ContextCheckpoint
from test_graph_runtime import context
from test_world_state_store import snapshot


def test_world_archive_collection_is_complete_or_refuses_overflow(tmp_path):
    store = WorldStateStore(tmp_path / "paper.sqlite3")
    original = snapshot()
    second = snapshot(original.as_of + timedelta(seconds=1))
    store.put(original)
    store.put(second)
    assert store.research_snapshots(limit=2) == (original, second)
    with pytest.raises(ValueError, match="bound"):
        store.research_snapshots(limit=1)


def test_world_archive_collection_refuses_changed_member_bytes(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = WorldStateStore(path)
    store.put(snapshot())
    with sqlite3.connect(path) as db:
        db.execute("UPDATE world_state_snapshots SET snapshot_json='{}'")
    with pytest.raises(ValueError, match="integrity"):
        store.research_snapshots()


def graph_records(path):
    registry = load_agent_catalog()
    compiled = asyncio.run(context(registry))
    checkpoint = ContextCheckpoint(
        schema_version="1.0", context=compiled, manifest=registry.get("A02", "0.1.0")
    )
    ledger = GraphLedger(path)
    first = ledger.create("archive-first", checkpoint)
    second = ledger.create("archive-second", checkpoint)
    return ledger, first, second


def test_agent_graph_collection_reads_all_matching_records_or_refuses_overflow(tmp_path):
    path = tmp_path / "graphs.sqlite3"
    ledger, first, second = graph_records(path)
    try:
        assert ledger.records_for_agent("A02", "0.1.0", limit=2) == (first, second)
        assert ledger.records_for_agent("A08", "0.1.0") == ()
        with pytest.raises(ValueError, match="bound"):
            ledger.records_for_agent("A02", "0.1.0", limit=1)
    finally:
        ledger.close()
    with GraphLedger.archive(path) as archive:
        assert archive.records_for_agent("A02", "0.1.0", limit=2) == (first, second)


def test_deleted_graph_member_is_detected_from_surviving_audit_identity(tmp_path):
    path = tmp_path / "graphs.sqlite3"
    ledger, _, _ = graph_records(path)
    try:
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM graph_runs WHERE run_id='archive-second'")
        with pytest.raises(ValueError, match="collection"):
            ledger.records_for_agent("A02", "0.1.0")
    finally:
        ledger.close()

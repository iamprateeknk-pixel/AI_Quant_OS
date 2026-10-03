import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3

import pytest

from ai_quant_os.incident_agent import proposal_for_context
from ai_quant_os.incident_proposal_store import IncidentProposalStore
from test_incident_sources import compile_context


def proposal(task="review"):
    return proposal_for_context(asyncio.run(compile_context(task=task)))


def test_actual_archive_reconstructs_exact_idempotent_proposal_on_restart(tmp_path):
    path = tmp_path / "proposals.sqlite3"
    record = proposal()
    store = IncidentProposalStore(path)
    assert store.put(record) == store.put(record) == record
    store.close()
    store = IncidentProposalStore(path)
    assert store.get(record.proposal_ref) == record
    store.close()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM incident_proposals").fetchone()[0] == 1


def test_concurrent_publish_commits_one_exact_owned_record(tmp_path):
    record = proposal()
    stores = [IncidentProposalStore(tmp_path / "proposals.sqlite3") for _ in range(2)]
    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda store: store.put(record), stores))
        assert results == [record, record]
        assert stores[0].get(record.proposal_ref) == record
    finally:
        for store in stores:
            store.close()


def test_capacity_failure_preserves_prior_proposal(tmp_path):
    record = proposal()
    store = IncidentProposalStore(tmp_path / "proposals.sqlite3", capacity=1)
    try:
        store.put(record)
        with pytest.raises(ValueError, match="capacity"):
            store.put(proposal("other"))
        assert store.get(record.proposal_ref) == record
    finally:
        store.close()


@pytest.mark.parametrize("damage", ["payload", "delete", "journal", "all_metadata"])
def test_changed_or_missing_archive_evidence_cannot_be_healed(tmp_path, damage):
    path = tmp_path / "proposals.sqlite3"
    record = proposal()
    store = IncidentProposalStore(path)
    store.put(record)
    store.close()
    with closing(sqlite3.connect(path)) as db, db:
        if damage == "payload":
            db.execute("UPDATE incident_proposals SET proposal_json='{}'")
        elif damage == "delete":
            db.execute("DELETE FROM incident_proposals")
        elif damage == "journal":
            db.execute("DELETE FROM audit_events")
        else:
            for table in ("incident_proposals", "audit_events", "audit_chain", "audit_head"):
                db.execute(f"DROP TABLE {table}")
    with pytest.raises((ValueError, sqlite3.Error)):
        IncidentProposalStore(path)


def test_existing_empty_file_is_not_rebaselined(tmp_path):
    path = tmp_path / "empty.sqlite3"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="metadata"):
        IncidentProposalStore(path)

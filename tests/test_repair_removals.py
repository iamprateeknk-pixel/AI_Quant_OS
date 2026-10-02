import importlib
import sqlite3

import pytest

from ai_quant_os.repair_jobs import prepared_repair_job
from test_repair_jobs import PROGRAM, SUITE_SHA, snapshot
from test_repair_sandbox import IMAGE
from test_repair_workspace import repository as repository

OWNER = "a" * 32
CID = "c" * 64


def module():
    assert importlib.util.find_spec("ai_quant_os.repair_removals") is not None
    return importlib.import_module("ai_quant_os.repair_removals")


def test_removal_requires_exact_prior_intent_and_survives_reopening(tmp_path, repository):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        with module().RemovalJournal(job.path.parent) as journal:
            with pytest.raises(ValueError, match="intent"):
                journal.removed(job, OWNER, CID)
            journal.intent(job, OWNER, CID, image_id=IMAGE)
            assert journal.confirmed(job, OWNER, image_id=IMAGE) is None
            journal.removed(job, OWNER, CID)
        with module().RemovalJournal(job.path.parent) as journal:
            assert journal.confirmed(job, OWNER, image_id=IMAGE) == CID
            journal.intent(job, OWNER, CID, image_id=IMAGE)
            journal.removed(job, OWNER, CID)
            assert journal.event_count == 2
            with pytest.raises(ValueError):
                journal.intent(job, OWNER, "d" * 64, image_id=IMAGE)
            assert journal.confirmed(job, OWNER, image_id="sha256:" + "f" * 64) is None


def test_audit_body_or_head_deletion_cannot_rebaseline_removal_proof(tmp_path, repository):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        with module().RemovalJournal(job.path.parent) as journal:
            journal.intent(job, OWNER, CID, image_id=IMAGE)
            journal.removed(job, OWNER, CID)
        path = job.path.parent / "repair-removals.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM audit_head")
        with pytest.raises(ValueError):
            with module().RemovalJournal(job.path.parent):
                pytest.fail("Deleted audit metadata was accepted")


def test_mutated_removal_acknowledgement_is_never_trusted(tmp_path, repository):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        with module().RemovalJournal(job.path.parent) as journal:
            journal.intent(job, OWNER, CID, image_id=IMAGE)
            journal.removed(job, OWNER, CID)
        path = job.path.parent / "repair-removals.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE audit_events SET event_json=replace(event_json,?,?) WHERE event_key LIKE '%:REMOVED'",
                (CID, "d" * 64),
            )
        with pytest.raises(ValueError):
            with module().RemovalJournal(job.path.parent):
                pytest.fail("Tampered removal was accepted")


def test_capacity_reserves_acknowledgement_room_for_every_admitted_intent(tmp_path, repository):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        with module().RemovalJournal(job.path.parent, capacity=1) as journal:
            journal.intent(job, OWNER, CID, image_id=IMAGE)
            with pytest.raises(ValueError, match="capacity"):
                journal.intent(job, "b" * 32, "d" * 64, image_id=IMAGE)
            journal.removed(job, OWNER, CID)
            assert journal.confirmed(job, OWNER, image_id=IMAGE) == CID

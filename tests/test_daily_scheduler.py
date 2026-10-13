from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

START = datetime(2026, 1, 2, tzinfo=timezone.utc)


def scheduler(tmp_path, clock, execution_ledger=None):
    from ai_quant_os.daily_scheduler import DailyScheduler
    from ai_quant_os.evidence_store import EvidenceStore
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS

    return DailyScheduler(
        EvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda: ((), ()),
        lambda: (),
        PAPER_INSTRUMENTS,
        lambda: clock[0],
        lambda: True,
        execution_ledger=execution_ledger,
    )


def test_automatic_boundary_publication_is_idempotent_and_survives_restart(tmp_path):
    clock = [START]
    worker = scheduler(tmp_path, clock)
    assert worker.step()["state"] == "WAITING"
    clock[0] += timedelta(days=1)
    status = worker.step()
    assert status["last_receipt"]["outcome"] == "PUBLISHED"
    report = worker.archive.daily(START.date())
    assert report.report.state == "COMPLETE_ACCOUNTING"
    assert report.report.promotion_allowed is False
    assert scheduler(tmp_path, clock).step()["completed_days"] == 1
    assert worker.archive.daily(START.date()) == report


def test_automatic_execution_publication_retries_after_transient_ledger_failure(tmp_path):
    clock = [START]
    calls = 0

    def execution_ledger():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary ledger read failure")
        return (), (), ()

    worker = scheduler(tmp_path, clock, execution_ledger)
    worker.step()
    clock[0] += timedelta(days=1)
    status = worker.step()
    assert status["published_days"] == 1
    assert status["execution_attribution_pending_days"] == 1
    assert status["execution_attribution_error"] == "EXECUTION_ATTRIBUTION_PENDING"
    assert worker.archive.daily_execution(START.date()) is None

    status = worker.step()
    assert status["execution_attribution_pending_days"] == 0
    assert status["execution_attribution_error"] is None
    assert worker.archive.daily_execution(START.date()).report.contribution.coverage == "EMPTY"
    assert calls == 2


def test_execution_retry_queue_is_reconstructed_after_restart(tmp_path):
    clock = [START]

    def unavailable_ledger():
        raise OSError("temporary ledger read failure")

    first = scheduler(tmp_path, clock, unavailable_ledger)
    first.step()
    clock[0] += timedelta(days=1)
    assert first.step()["execution_attribution_pending_days"] == 1

    restarted = scheduler(tmp_path, clock, lambda: ((), (), ()))
    status = restarted.step()
    assert status["execution_attribution_pending_days"] == 0
    assert status["execution_attribution_error"] is None
    assert restarted.archive.daily_execution(START.date()).report.contribution.coverage == "EMPTY"


def test_cached_execution_retry_halts_when_paper_audit_integrity_fails(tmp_path):
    clock = [START]
    reads = 0

    def execution_ledger():
        nonlocal reads
        reads += 1
        if reads == 1:
            raise OSError("temporary ledger read failure")
        return (), (), ()

    worker = scheduler(tmp_path, clock, execution_ledger)
    worker.step()
    clock[0] += timedelta(days=1)
    assert worker.step()["execution_attribution_pending_days"] == 1
    assert reads == 1

    worker.integrity = lambda: False
    status = worker.step()
    assert status["state"] == "HALTED_AUDIT"
    assert status["execution_attribution_pending_days"] == 1
    assert worker.archive.daily_execution(START.date()) is None
    assert reads == 1


def test_downtime_records_missed_boundaries_without_fake_capture(tmp_path):
    clock = [START]
    worker = scheduler(tmp_path, clock)
    worker.step()
    clock[0] += timedelta(days=3, hours=2)
    status = scheduler(tmp_path, clock).step()
    assert status["completed_days"] == 3
    assert status["last_receipt"]["outcome"] == "MISSED"
    assert worker.archive.daily(START.date()) is None
    assert worker.archive.get_checkpoint("daily-auto-2026-01-03") is None
    assert worker.archive.get_checkpoint("daily-auto-2026-01-05") is not None


def test_crash_after_capture_is_resumed_without_replacing_source(tmp_path, monkeypatch):
    clock = [START]
    worker = scheduler(tmp_path, clock)
    worker.step()
    clock[0] += timedelta(days=1)
    original = worker.schedule.record

    def unavailable(*args):
        raise sqlite3.OperationalError("Interrupted receipt commit")

    monkeypatch.setattr(worker.schedule, "record", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        worker.step()
    checkpoint = worker.archive.get_checkpoint("daily-auto-2026-01-03")
    report = worker.archive.daily(START.date())
    assert report is not None
    monkeypatch.setattr(worker.schedule, "record", original)
    clock[0] += timedelta(seconds=20)
    assert worker.step()["completed_days"] == 1
    assert worker.archive.get_checkpoint(checkpoint.checkpoint_id) == checkpoint
    assert worker.archive.daily(START.date()) == report


def test_audit_failure_and_clock_regression_do_not_create_evidence(tmp_path):
    clock = [START]
    worker = scheduler(tmp_path, clock)
    worker.integrity = lambda: False
    assert worker.step()["state"] == "HALTED_AUDIT"
    assert worker.archive.get_checkpoint("daily-auto-2026-01-02") is None
    worker.integrity = lambda: True
    worker.step()
    clock[0] -= timedelta(seconds=1)
    with pytest.raises(ValueError, match="clock"):
        worker.step()


def test_scheduler_receipt_corruption_is_detected(tmp_path):
    clock = [START]
    worker = scheduler(tmp_path, clock)
    worker.step()
    clock[0] += timedelta(days=1)
    worker.step()
    with sqlite3.connect(worker.archive.path) as db:
        db.execute("UPDATE evidence_schedule_receipts SET blob=?", (b"{}",))
    with pytest.raises(ValueError, match="fingerprint"):
        scheduler(tmp_path, clock).status()


def test_enabled_application_runs_and_stops_worker_with_protected_status(tmp_path):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app
    import time

    clock = [START]
    app = create_app(
        operator_token="scheduler-test",
        paper_db_path=tmp_path / "paper.sqlite3",
        clock=lambda: clock[0],
        daily_evidence_enabled=True,
    )
    headers = {"X-Operator-Token": "scheduler-test"}
    url = "/api/v1/paper/evidence/scheduler"
    with TestClient(app) as client:
        assert client.get(url).status_code == 401
        deadline = time.monotonic() + 5
        while client.get(url, headers=headers).json().get("current_boundary_captured_at") is None:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        clock[0] += timedelta(days=1)
        while client.get(url, headers=headers).json().get("completed_days", 0) < 1:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert (
            client.get("/api/v1/paper/evidence/daily/2026-01-02", headers=headers).status_code
            == 200
        )
        execution_url = "/api/v1/paper/evidence/daily/2026-01-02/execution-attribution"
        assert client.get(execution_url, headers=headers).status_code == 200
        assert (
            client.get(execution_url, headers=headers).json()["report"]["contribution"]["coverage"]
            == "EMPTY"
        )
    assert app.state.daily_task is None


def test_scheduler_requires_configured_persistence_and_operator(tmp_path):
    from ai_quant_os.api import create_app

    with pytest.raises(ValueError):
        create_app(daily_evidence_enabled=True)
    with pytest.raises(ValueError):
        create_app(paper_db_path=tmp_path / "paper.sqlite3", daily_evidence_enabled=True)


def test_scheduler_retains_real_boundary_values_and_withholds_late_marks(tmp_path):
    from test_daily_evidence import checkpoints
    from ai_quant_os.daily_scheduler import DailyScheduler
    from ai_quant_os.evidence_store import EvidenceStore

    opening, closing = checkpoints()
    clock = [opening.captured_at + timedelta(seconds=10)]
    worker = DailyScheduler(
        EvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda: (
            (opening.owners, opening.fills)
            if clock[0].date() == opening.captured_at.date()
            else (closing.owners, closing.fills)
        ),
        lambda: opening.quotes if clock[0].date() == opening.captured_at.date() else closing.quotes,
        opening.catalog,
        lambda: clock[0],
        lambda: True,
    )
    worker.step()
    clock[0] = closing.captured_at
    status = worker.step()
    assert status["published_days"] == 1
    evidence = worker.archive.daily(opening.captured_at.date())
    assert evidence.report.state == "INCOMPLETE_MARKS"
    assert evidence.report.instruments[0].opening_mark_state == "CAPTURE_LATE"
    assert evidence.report.instruments[0].daily_pnl_quote is None


def test_bounded_catchup_and_deleted_head_are_observable(tmp_path):
    clock = [START]
    worker = scheduler(tmp_path, clock)
    worker.step()
    clock[0] += timedelta(days=10)
    status = worker.step()
    assert status["completed_days"] == 7 and status["pending_days"] == 3
    assert worker.step()["missed_days"] == 10
    with sqlite3.connect(worker.archive.path) as db:
        db.execute("DELETE FROM evidence_schedule_head")
    with pytest.raises(ValueError, match="head"):
        scheduler(tmp_path, clock).status()


def test_automatic_identifiers_and_invalid_environment_are_refused(tmp_path, monkeypatch):
    from ai_quant_os.api import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(operator_token="scheduler-test", paper_db_path=tmp_path / "paper.sqlite3")
    )
    assert (
        client.post(
            "/api/v1/paper/evidence/checkpoints",
            headers={"X-Operator-Token": "scheduler-test"},
            json={"checkpoint_id": "daily-auto-2026-01-02"},
        ).status_code
        == 409
    )
    monkeypatch.setenv("AI_QUANT_DAILY_EVIDENCE_ENABLED", "invalid")
    with pytest.raises(ValueError, match="must be 0 or 1"):
        create_app()


def test_unexpected_callback_failure_is_visible_and_sanitized(tmp_path, caplog):
    import asyncio
    from ai_quant_os.daily_scheduler import run_daily_scheduler

    worker = scheduler(tmp_path, [START])

    def fail():
        raise RuntimeError("private benchmark context")

    worker.ledger = fail

    async def exercise():
        task = asyncio.create_task(run_daily_scheduler(worker))
        deadline = asyncio.get_running_loop().time() + 5
        while worker.last_error is None and not task.done():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert worker.last_error == "SCHEDULER_UNEXPECTED_FAILURE"

    asyncio.run(exercise())
    assert "private benchmark context" not in caplog.text

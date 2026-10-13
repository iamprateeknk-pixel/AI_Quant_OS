"""Opt-in local UTC boundary worker; no historical price fabrication or capital tools."""

import asyncio
from dataclasses import asdict
from datetime import timedelta, timezone
from hashlib import sha256
import json
import logging
import sqlite3

from ai_quant_os.contracts import _require_aware
from ai_quant_os.daily_evidence import DailyEvidence, EvidenceCheckpoint, build_daily_report
from ai_quant_os.evidence_schedule_store import EvidenceScheduleStore, ScheduleReceipt
from ai_quant_os.evidence_store import encode_evidence

LOGGER = logging.getLogger(__name__)


def boundary_id(day):
    return "daily-auto-" + day.isoformat()


class DailyScheduler:
    def __init__(self, archive, ledger, quotes, catalog, clock, integrity, execution_ledger=None):
        self.archive, self.ledger, self.quotes, self.catalog, self.clock, self.integrity = (
            archive,
            ledger,
            quotes,
            catalog,
            clock,
            integrity,
        )
        self.execution_ledger = execution_ledger
        self._execution_pending = []
        self._execution_queue_loaded = False
        self.execution_last_error = None
        self.schedule = EvidenceScheduleStore(archive)
        self.last_error = None
        self._last_clock = None
        self._waiting_day = None
        self._cached_status = None

    def status(self):
        started, receipts = self.schedule.state()
        now = self.clock()
        _require_aware(now)
        now = now.astimezone(timezone.utc)
        boundary = self.archive.get_checkpoint(boundary_id(now.date()))
        pending = (
            0
            if started is None
            else max(0, (now.date() - started.started_at.date()).days - len(receipts))
        )
        return dict(
            schema_version="1.0",
            enabled=True,
            state="ERROR"
            if self.last_error
            else "STARTING"
            if boundary is None
            else "CATCHING_UP"
            if pending
            else "WAITING",
            started_at=None if started is None else started.started_at,
            completed_days=len(receipts),
            published_days=sum(row.outcome == "PUBLISHED" for row in receipts),
            missed_days=sum(row.outcome == "MISSED" for row in receipts),
            failed_days=sum(row.outcome == "FAILED" for row in receipts),
            pending_days=pending,
            last_receipt=None if not receipts else asdict(receipts[-1]),
            last_error=self.last_error,
            current_boundary_captured_at=None if boundary is None else boundary.captured_at,
            execution_attribution_pending_days=len(self._execution_pending),
            execution_attribution_error=self.execution_last_error,
        )

    def _load_execution_queue(self, receipts):
        if self.execution_ledger is None or self._execution_queue_loaded:
            return
        self._execution_pending = [
            receipt.day
            for receipt in receipts
            if receipt.outcome == "PUBLISHED" and self.archive.daily_execution(receipt.day) is None
        ]
        self._execution_queue_loaded = True

    def _publish_pending_execution(self):
        if self.execution_ledger is None or not self._execution_pending:
            return
        day = self._execution_pending[0]
        try:
            daily = self.archive.daily(day)
            if daily is None:
                raise ValueError("Published daily execution source is missing")
            owners, fills, benchmarks = self.execution_ledger()
            from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence

            evidence = build_daily_execution_evidence(
                daily, owners, fills, benchmarks, published_at=self.clock()
            )
            self.archive.publish_daily_execution(evidence)
            self._execution_pending.pop(0)
            self.execution_last_error = None
        except Exception as error:
            if self.execution_last_error is None:
                LOGGER.error(
                    json.dumps(
                        {
                            "event": "daily_execution_attribution_pending",
                            "reason": "SOURCE_OR_PUBLICATION_UNAVAILABLE",
                            "exception_type": type(error).__name__,
                        }
                    )
                )
            self.execution_last_error = "EXECUTION_ATTRIBUTION_PENDING"

    def _capture(self, day):
        identifier = boundary_id(day)
        existing = self.archive.get_checkpoint(identifier)
        if existing is not None:
            if existing.captured_at.date() != day:
                raise ValueError("Automatic boundary checkpoint date mismatch")
            return existing
        now = self.clock().astimezone(timezone.utc)
        if now.date() != day:
            raise ValueError("Boundary clock changed while capturing")
        owners, fills = self.ledger()
        checkpoint = EvidenceCheckpoint(identifier, now, owners, fills, self.quotes(), self.catalog)
        try:
            return self.archive.checkpoint(checkpoint)
        except ValueError:
            winner = self.archive.get_checkpoint(identifier)
            if winner is None or winner.captured_at.date() != day:
                raise
            return winner

    def step(self):
        now = self.clock()
        _require_aware(now)
        now = now.astimezone(timezone.utc)
        if self._last_clock is not None and now < self._last_clock:
            raise ValueError("Scheduler clock regressed")
        self._last_clock = now
        if self.execution_ledger is not None and not self._execution_queue_loaded:
            _, receipts = self.schedule.state()
            self._load_execution_queue(receipts)
        if not self.integrity():
            self.last_error = "PAPER_AUDIT_INTEGRITY_FAILED"
            return {**self.status(), "state": "HALTED_AUDIT"}
        if (
            self._waiting_day == now.date()
            and self._cached_status is not None
            and self.last_error is None
        ):
            self._publish_pending_execution()
            self._cached_status = self.status()
            return self._cached_status
        started = self.schedule.start(now)
        _, receipts = self.schedule.state()
        self._load_execution_queue(receipts)
        if now < started.started_at or now.date() < started.started_at.date() + timedelta(
            days=len(receipts)
        ):
            raise ValueError("Scheduler clock precedes recorded history")
        self._capture(now.date())
        next_day = started.started_at.date() + timedelta(days=len(receipts))
        for _ in range(7):
            if next_day >= now.date():
                break
            opening = self.archive.get_checkpoint(boundary_id(next_day))
            closing = self.archive.get_checkpoint(boundary_id(next_day + timedelta(days=1)))
            if opening is None or closing is None:
                receipt = ScheduleReceipt(
                    next_day, now, "MISSED", "BOUNDARY_CHECKPOINT_MISSING", None
                )
            else:
                try:
                    report = build_daily_report(next_day, opening, closing)
                    evidence = self.archive.publish(
                        DailyEvidence(opening, closing, report, self.clock())
                    )
                    receipt = ScheduleReceipt(
                        next_day,
                        now,
                        "PUBLISHED",
                        None,
                        sha256(encode_evidence(evidence)).hexdigest(),
                    )
                except ValueError:
                    receipt = ScheduleReceipt(
                        next_day, now, "FAILED", "PUBLICATION_CONFLICT_OR_LINEAGE_ERROR", None
                    )
            self.schedule.record(receipt)
            if receipt.outcome == "PUBLISHED" and self.execution_ledger is not None:
                self._execution_pending.append(next_day)
            LOGGER.info(
                json.dumps(
                    {
                        "event": "daily_evidence_scheduler_day",
                        "day": next_day.isoformat(),
                        "outcome": receipt.outcome,
                        "reason": receipt.reason,
                    }
                )
            )
            next_day += timedelta(days=1)
        self.last_error = None
        self._publish_pending_execution()
        status = self.status()
        self._waiting_day = now.date() if status["pending_days"] == 0 else None
        self._cached_status = status
        return status


def _record_error(worker, error):
    known = isinstance(
        error, (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error)
    )
    code = "SCHEDULER_VERIFICATION_OR_STORAGE_FAILURE" if known else "SCHEDULER_UNEXPECTED_FAILURE"
    if worker.last_error != code:
        LOGGER.error(
            json.dumps(
                {
                    "event": "daily_evidence_scheduler_error",
                    "reason": code,
                    "exception_type": type(error).__name__,
                }
            )
        )
    worker.last_error = code


async def run_daily_scheduler(worker):
    delay = 1
    while True:
        job = asyncio.create_task(asyncio.to_thread(worker.step))
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            # Finish an in-flight SQLite operation before application shutdown.
            try:
                await job
            except Exception as error:
                _record_error(worker, error)
            raise
        except Exception as error:
            _record_error(worker, error)
        await asyncio.sleep(delay)
        delay = min(60, delay * 2) if worker.last_error is not None else 1

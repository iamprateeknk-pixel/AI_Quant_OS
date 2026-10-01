"""Durable activation and terminal daily scheduler receipts with a committed chain head."""

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import re
import sqlite3
from typing import Literal

from ai_quant_os.contracts import _require_aware
from ai_quant_os.evidence_store import _decode, encode_evidence


@dataclass(frozen=True)
class ScheduleStart:
    started_at: datetime

    def __post_init__(self):
        _require_aware(self.started_at)
        object.__setattr__(self, "started_at", self.started_at.astimezone(timezone.utc))


@dataclass(frozen=True)
class ScheduleReceipt:
    day: date
    attempted_at: datetime
    outcome: Literal["PUBLISHED", "MISSED", "FAILED"]
    reason: str | None
    report_sha256: str | None

    def __post_init__(self):
        _require_aware(self.attempted_at)
        object.__setattr__(self, "attempted_at", self.attempted_at.astimezone(timezone.utc))
        if type(self.day) is not date or self.attempted_at.date() <= self.day:
            raise ValueError("Scheduler receipts require a closed day")
        if self.outcome == "PUBLISHED":
            if (
                self.reason is not None
                or not isinstance(self.report_sha256, str)
                or not re.fullmatch(r"[a-f0-9]{64}", self.report_sha256)
            ):
                raise ValueError("Published scheduler receipt requires report identity")
        elif (self.outcome, self.reason) not in (
            ("MISSED", "BOUNDARY_CHECKPOINT_MISSING"),
            ("FAILED", "PUBLICATION_CONFLICT_OR_LINEAGE_ERROR"),
        ) or self.report_sha256 is not None:
            raise ValueError("Invalid scheduler failure receipt")


@dataclass(frozen=True)
class ScheduleEvent:
    receipt: ScheduleReceipt
    previous_sha256: str


class EvidenceScheduleStore:
    def __init__(self, archive):
        self.archive = archive
        with closing(sqlite3.connect(archive.path, timeout=10)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS evidence_schedule_start (singleton INTEGER PRIMARY KEY CHECK(singleton=1),blob BLOB NOT NULL,sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence_schedule_receipts (day TEXT PRIMARY KEY,blob BLOB NOT NULL,sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence_schedule_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1),count INTEGER NOT NULL,sha256 TEXT NOT NULL);
            """)
            db.commit()

    def _state(self, db):
        row = db.execute(
            "SELECT blob,sha256 FROM evidence_schedule_start WHERE singleton=1"
        ).fetchone()
        rows = db.execute(
            "SELECT day,blob,sha256 FROM evidence_schedule_receipts ORDER BY day"
        ).fetchall()
        head = db.execute(
            "SELECT count,sha256 FROM evidence_schedule_head WHERE singleton=1"
        ).fetchone()
        if row is None:
            if rows or head is not None:
                raise ValueError("Scheduler activation history missing")
            return None, ()
        started = _decode(*row, ScheduleStart)
        if len(rows) > 730:
            raise ValueError("Scheduler receipt capacity exceeded")
        previous, receipts = row[1], []
        for index, (day, raw, digest) in enumerate(rows):
            event = _decode(raw, digest, ScheduleEvent)
            receipt = event.receipt
            if (
                day != receipt.day.isoformat()
                or receipt.day != started.started_at.date() + timedelta(days=index)
                or event.previous_sha256 != previous
            ):
                raise ValueError("Scheduler receipt chain mismatch")
            if receipt.outcome == "PUBLISHED":
                evidence = self.archive._daily(db, receipt.day)
                if (
                    evidence is None
                    or sha256(encode_evidence(evidence)).hexdigest() != receipt.report_sha256
                ):
                    raise ValueError("Scheduler published report binding mismatch")
            previous = digest
            receipts.append(receipt)
        if head != (len(rows), previous):
            raise ValueError("Scheduler committed head mismatch")
        return started, tuple(receipts)

    def state(self):
        with closing(sqlite3.connect(self.archive.path, timeout=10)) as db:
            db.execute("BEGIN")
            return self._state(db)

    def start(self, now):
        raw = encode_evidence(ScheduleStart(now))
        with closing(sqlite3.connect(self.archive.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            current, _ = self._state(db)
            if current is not None:
                return current
            digest = sha256(raw).hexdigest()
            db.execute("INSERT INTO evidence_schedule_start VALUES (1,?,?)", (raw, digest))
            db.execute("INSERT INTO evidence_schedule_head VALUES (1,0,?)", (digest,))
            db.commit()
            return ScheduleStart(now)

    def record(self, receipt):
        with closing(sqlite3.connect(self.archive.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            started, receipts = self._state(db)
            if started is None:
                raise ValueError("Scheduler activation required")
            index = (receipt.day - started.started_at.date()).days
            if 0 <= index < len(receipts):
                prior = receipts[index]
                if (prior.outcome, prior.reason, prior.report_sha256) != (
                    receipt.outcome,
                    receipt.reason,
                    receipt.report_sha256,
                ):
                    raise ValueError("Immutable scheduler receipt cannot change")
                return prior
            if index != len(receipts) or len(receipts) >= 730:
                raise ValueError("Scheduler receipt sequence/capacity conflict")
            count, previous = db.execute(
                "SELECT count,sha256 FROM evidence_schedule_head WHERE singleton=1"
            ).fetchone()
            raw = encode_evidence(ScheduleEvent(receipt, previous))
            _decode(raw, sha256(raw).hexdigest(), ScheduleEvent)
            digest = sha256(raw).hexdigest()
            db.execute(
                "INSERT INTO evidence_schedule_receipts VALUES (?,?,?)",
                (receipt.day.isoformat(), raw, digest),
            )
            db.execute(
                "UPDATE evidence_schedule_head SET count=?,sha256=? WHERE singleton=1",
                (count + 1, digest),
            )
            self._state(db)
            db.commit()
            return receipt

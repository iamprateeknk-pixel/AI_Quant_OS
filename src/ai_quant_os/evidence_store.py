"""Immutable local paper checkpoints and source-bound daily publication."""

from contextlib import closing
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from pydantic import TypeAdapter

from ai_quant_os.daily_evidence import DailyEvidence, EvidenceCheckpoint
from ai_quant_os.daily_execution_evidence import DailyExecutionEvidence


class EvidenceIntegrityError(ValueError):
    """Stored source bytes or cross-record bindings failed verification."""


class EvidenceConflictError(ValueError):
    """A valid immutable evidence record already exists with different sources."""


def encode_evidence(value):
    record = json.loads(TypeAdapter(type(value)).dump_json(value))
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _decode(raw, digest, model):
    if (
        not isinstance(raw, bytes)
        or len(raw) > 16 * 1024 * 1024
        or sha256(raw).hexdigest() != digest
    ):
        raise EvidenceIntegrityError("Paper evidence archive fingerprint mismatch")
    try:
        value = TypeAdapter(model).validate_json(raw)
        if encode_evidence(value) != raw:
            raise EvidenceIntegrityError("Paper evidence archive is not canonical")
    except EvidenceIntegrityError:
        raise
    except (TypeError, ValueError, KeyError) as error:
        raise EvidenceIntegrityError("Paper evidence archive cannot be decoded") from error
    return value


class EvidenceStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS evidence_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY, blob BLOB NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_evidence (
                    day TEXT PRIMARY KEY, blob BLOB NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_execution_evidence (
                    day TEXT PRIMARY KEY, blob BLOB NOT NULL, sha256 TEXT NOT NULL
                );
            """)
            db.commit()

    @staticmethod
    def _capacity(db, additional, table):
        limits = {
            "evidence_checkpoints": 1000,
            "daily_evidence": 730,
            "daily_execution_evidence": 730,
        }
        if table not in limits:
            raise ValueError("Unknown paper evidence archive table")
        counts = {
            name: db.execute(
                f"SELECT COUNT(*),COALESCE(SUM(length(blob)),0) FROM {name}"
            ).fetchone()
            for name in limits
        }
        if (
            counts[table][0] >= limits[table]
            or sum(row[1] for row in counts.values()) + additional > 64 * 1024 * 1024
        ):
            raise ValueError("Paper evidence archive capacity exceeded")

    @staticmethod
    def _checkpoint(db, identifier):
        row = db.execute(
            "SELECT blob,sha256 FROM evidence_checkpoints WHERE checkpoint_id=?", (identifier,)
        ).fetchone()
        if row is None:
            return None
        value = _decode(*row, EvidenceCheckpoint)
        if value.checkpoint_id != identifier:
            raise EvidenceIntegrityError("Paper checkpoint identity mismatch")
        return value

    def get_checkpoint(self, identifier):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            return self._checkpoint(db, identifier)

    def checkpoint(self, value):
        raw = encode_evidence(value)
        value = _decode(raw, sha256(raw).hexdigest(), EvidenceCheckpoint)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = self._checkpoint(db, value.checkpoint_id)
            if prior is not None:
                if prior != value:
                    raise ValueError("Immutable paper checkpoint cannot change")
                return prior
            self._capacity(db, len(raw), "evidence_checkpoints")
            db.execute(
                "INSERT INTO evidence_checkpoints VALUES (?,?,?)",
                (value.checkpoint_id, raw, sha256(raw).hexdigest()),
            )
            db.commit()
            return value

    def _daily(self, db, day):
        row = db.execute(
            "SELECT blob,sha256 FROM daily_evidence WHERE day=?", (day.isoformat(),)
        ).fetchone()
        if row is None:
            return None
        value = _decode(*row, DailyEvidence)
        if (
            value.report.day != day
            or self._checkpoint(db, value.opening.checkpoint_id) != value.opening
            or self._checkpoint(db, value.closing.checkpoint_id) != value.closing
        ):
            raise EvidenceIntegrityError("Daily paper evidence source binding mismatch")
        return value

    def daily(self, day):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            return self._daily(db, day)

    def _daily_execution(self, db, day):
        row = db.execute(
            "SELECT blob,sha256 FROM daily_execution_evidence WHERE day=?",
            (day.isoformat(),),
        ).fetchone()
        if row is None:
            return None
        value = _decode(*row, DailyExecutionEvidence)
        if value.report.day != day or self._daily(db, day) != value.daily:
            raise EvidenceIntegrityError("Daily execution evidence source binding mismatch")
        return value

    def daily_execution(self, day):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            return self._daily_execution(db, day)

    def daily_execution_range(self, start_day, end_day):
        if (
            type(start_day) is not date
            or type(end_day) is not date
            or end_day < start_day
            or (end_day - start_day).days >= 366
        ):
            raise ValueError("Daily execution range must span at most 366 days")
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            values = []
            for offset in range((end_day - start_day).days + 1):
                day = start_day + timedelta(days=offset)
                value = self._daily_execution(db, day)
                if value is not None:
                    values.append(value)
            return tuple(values)

    def publish(self, value):
        raw = encode_evidence(value)
        value = _decode(raw, sha256(raw).hexdigest(), DailyEvidence)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                self._checkpoint(db, value.opening.checkpoint_id) != value.opening
                or self._checkpoint(db, value.closing.checkpoint_id) != value.closing
            ):
                raise ValueError("Daily evidence requires its stored checkpoints")
            prior = self._daily(db, value.report.day)
            if prior is not None:
                if (prior.opening, prior.closing, prior.report) != (
                    value.opening,
                    value.closing,
                    value.report,
                ):
                    raise ValueError("Immutable daily evidence cannot change")
                return prior
            self._capacity(db, len(raw), "daily_evidence")
            db.execute(
                "INSERT INTO daily_evidence VALUES (?,?,?)",
                (value.report.day.isoformat(), raw, sha256(raw).hexdigest()),
            )
            db.commit()
            return value

    def publish_daily_execution(self, value):
        raw = encode_evidence(value)
        value = _decode(raw, sha256(raw).hexdigest(), DailyExecutionEvidence)
        day = value.report.day
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            if self._daily(db, day) != value.daily:
                raise EvidenceConflictError(
                    "Daily execution evidence requires its published daily source"
                )
            prior = self._daily_execution(db, day)
            if prior is not None:
                if (
                    prior.daily,
                    prior.owners,
                    prior.fills,
                    prior.benchmarks,
                    prior.report,
                ) != (
                    value.daily,
                    value.owners,
                    value.fills,
                    value.benchmarks,
                    value.report,
                ):
                    raise EvidenceConflictError("Immutable daily execution evidence cannot change")
                return prior
            self._capacity(db, len(raw), "daily_execution_evidence")
            db.execute(
                "INSERT INTO daily_execution_evidence VALUES (?,?,?)",
                (day.isoformat(), raw, sha256(raw).hexdigest()),
            )
            db.commit()
            return value

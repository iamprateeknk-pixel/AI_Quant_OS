"""Immutable local paper checkpoints and source-bound daily publication."""

from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from pydantic import TypeAdapter

from ai_quant_os.daily_evidence import DailyEvidence, EvidenceCheckpoint


def encode_evidence(value):
    record = json.loads(TypeAdapter(type(value)).dump_json(value))
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _decode(raw, digest, model):
    if (
        not isinstance(raw, bytes)
        or len(raw) > 16 * 1024 * 1024
        or sha256(raw).hexdigest() != digest
    ):
        raise ValueError("Paper evidence archive fingerprint mismatch")
    value = TypeAdapter(model).validate_json(raw)
    if encode_evidence(value) != raw:
        raise ValueError("Paper evidence archive is not canonical")
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
            """)
            db.commit()

    @staticmethod
    def _capacity(db, additional, table):
        counts = [
            db.execute(f"SELECT COUNT(*),COALESCE(SUM(length(blob)),0) FROM {name}").fetchone()
            for name in ("evidence_checkpoints", "daily_evidence")
        ]
        limit = 1000 if table == "evidence_checkpoints" else 730
        if (
            counts[0 if table == "evidence_checkpoints" else 1][0] >= limit
            or sum(row[1] for row in counts) + additional > 64 * 1024 * 1024
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
            raise ValueError("Paper checkpoint identity mismatch")
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
            raise ValueError("Daily paper evidence source binding mismatch")
        return value

    def daily(self, day):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            return self._daily(db, day)

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

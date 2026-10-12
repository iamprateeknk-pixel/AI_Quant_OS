"""Owned once-only reservations and verified immutable diagnostic archives."""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
from uuid import uuid4

from ai_quant_os.counterfactual_results import CounterfactualReport, ReplayInputs
from ai_quant_os.research_store import _decode, encode_research


class CounterfactualStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS counterfactual_replays (
                replay_id TEXT PRIMARY KEY, owner_token TEXT NOT NULL,
                inputs BLOB NOT NULL, inputs_sha256 TEXT NOT NULL,
                report BLOB, report_sha256 TEXT
            )""")
            db.commit()

    @staticmethod
    def _verified(row):
        identifier, token, raw, digest, report, report_digest = row
        inputs = _decode(raw, digest, ReplayInputs)
        if identifier != inputs.plan.replay_id or not isinstance(token, str) or len(token) != 32:
            raise ValueError("Counterfactual reservation identity mismatch")
        if report is None:
            if report_digest is not None:
                raise ValueError("Counterfactual unfinished archive mismatch")
            return inputs, None
        value = _decode(report, report_digest, CounterfactualReport)
        if value.inputs != inputs:
            raise ValueError("Counterfactual archive inputs mismatch")
        return inputs, value

    def get(self, replay_id):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            row = db.execute(
                "SELECT * FROM counterfactual_replays WHERE replay_id=?", (replay_id,)
            ).fetchone()
            return None if row is None else self._verified(row)[1]

    @classmethod
    def read_existing(cls, path: Path, replay_id: str):
        """Verify a retained report through a SQLite read-only connection."""
        resolved = path.resolve(strict=True)
        with closing(sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute(
                "SELECT * FROM counterfactual_replays WHERE replay_id=?", (replay_id,)
            ).fetchone()
        return None if row is None else cls._verified(row)[1]

    def reserve(self, inputs: ReplayInputs):
        raw = encode_research(inputs)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("Counterfactual inputs exceed capacity")
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM counterfactual_replays WHERE replay_id=?", (inputs.plan.replay_id,)
            ).fetchone()
            if row is not None:
                previous, report = self._verified(row)
                if previous != inputs or row[2] != raw:
                    raise ValueError("Immutable counterfactual replay cannot change")
                if report is None:
                    raise ValueError("Uncertain counterfactual reservation requires investigation")
                return None, report
            count, size = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(inputs)+COALESCE(length(report),0)),0) FROM counterfactual_replays"
            ).fetchone()
            if count >= 1000 or size + len(raw) > 64 * 1024 * 1024:
                raise ValueError("Counterfactual archive capacity exceeded")
            token = uuid4().hex
            db.execute(
                "INSERT INTO counterfactual_replays VALUES (?,?,?,?,NULL,NULL)",
                (inputs.plan.replay_id, token, raw, sha256(raw).hexdigest()),
            )
            db.commit()
            return token, None

    def finish(self, token, report: CounterfactualReport):
        raw = encode_research(report)
        # Re-decode at the persistence boundary rather than trusting mutable callers.
        report = _decode(raw, sha256(raw).hexdigest(), CounterfactualReport)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM counterfactual_replays WHERE replay_id=?",
                (report.inputs.plan.replay_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Missing counterfactual reservation")
            inputs, existing = self._verified(row)
            if row[1] != token or existing is not None or inputs != report.inputs:
                raise ValueError("Counterfactual reservation ownership mismatch")
            size = db.execute(
                "SELECT COALESCE(SUM(length(inputs)+COALESCE(length(report),0)),0) FROM counterfactual_replays"
            ).fetchone()[0]
            if size + len(raw) > 64 * 1024 * 1024:
                raise ValueError("Counterfactual archive capacity exceeded")
            db.execute(
                "UPDATE counterfactual_replays SET report=?, report_sha256=? WHERE replay_id=?",
                (raw, sha256(raw).hexdigest(), report.inputs.plan.replay_id),
            )
            db.commit()

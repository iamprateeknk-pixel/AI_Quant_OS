"""Transactional experiment reservations, frozen selections and immutable result archives."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from pydantic import TypeAdapter

from ai_quant_os.research_contracts import Hypothesis, ResearchPlan
from ai_quant_os.research_results import ResearchReport, ResearchSelection


def encode_research(value) -> bytes:
    record = json.loads(TypeAdapter(type(value)).dump_json(value))
    _preserve_v10_encoding(record)
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _preserve_v10_encoding(record):
    if isinstance(record, dict):
        if record.get("schema_version") == "1.0":
            for key in ("monte_carlo", "monte_carlo_spec"):
                if key in record and record[key] is None:
                    del record[key]
        for value in record.values():
            _preserve_v10_encoding(value)
    elif isinstance(record, list):
        for value in record:
            _preserve_v10_encoding(value)


def _decode(blob, digest, model):
    if (
        not isinstance(blob, bytes)
        or len(blob) > 16 * 1024 * 1024
        or sha256(blob).hexdigest() != digest
    ):
        raise ValueError("Research archive fingerprint mismatch")
    value = TypeAdapter(model).validate_json(blob)
    if encode_research(value) != blob:
        raise ValueError("Research archive is not canonical")
    return value


def _micros(value):
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _overlaps(first, second):
    return (
        first.instrument_id == second.instrument_id
        and first.first_observed_at <= second.last_observed_at
        and first.last_observed_at >= second.first_observed_at
    )


@dataclass(frozen=True)
class ResearchReservation:
    token: str | None
    cached_report: ResearchReport | None


class ResearchStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_hypotheses (
                    hypothesis_id TEXT PRIMARY KEY, blob BLOB NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_experiments (
                    experiment_id TEXT PRIMARY KEY, owner_token TEXT NOT NULL,
                    plan BLOB NOT NULL, plan_sha256 TEXT NOT NULL,
                    selection BLOB, selection_sha256 TEXT,
                    report BLOB, report_sha256 TEXT
                );
                CREATE TABLE IF NOT EXISTS research_holdout_claims (
                    experiment_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL,
                    start_us INTEGER NOT NULL, end_us INTEGER NOT NULL, scope BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS research_holdout_intervals
                    ON research_holdout_claims (instrument_id,start_us,end_us);
            """)

    def _row(self, db, experiment_id):
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM research_experiments WHERE experiment_id=?", (experiment_id,)
        ).fetchone()
        if row is not None:
            plan = _decode(row["plan"], row["plan_sha256"], ResearchPlan)
            if plan.experiment_id != experiment_id:
                raise ValueError("Research archive identity mismatch")
            hypothesis = db.execute(
                "SELECT blob, sha256 FROM research_hypotheses WHERE hypothesis_id=?",
                (plan.hypothesis.hypothesis_id,),
            ).fetchone()
            if (
                hypothesis is None
                or _decode(hypothesis[0], hypothesis[1], Hypothesis) != plan.hypothesis
            ):
                raise ValueError("Research hypothesis lineage mismatch")
            claim = db.execute(
                "SELECT scope FROM research_holdout_claims WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if claim is None or claim[0] != encode_research(plan.holdout_scope):
                raise ValueError("Research holdout claim lineage mismatch")
        return row

    def _report(self, row):
        if row is None or row["report"] is None:
            return None
        report = _decode(row["report"], row["report_sha256"], ResearchReport)
        if encode_research(report.plan) != row["plan"]:
            raise ValueError("Research report plan mismatch")
        if report.selection is not None:
            if row["selection"] is None or encode_research(report.selection) != row["selection"]:
                raise ValueError("Research report selection mismatch")
            _decode(row["selection"], row["selection_sha256"], ResearchSelection)
        elif row["selection"] is not None:
            raise ValueError("Research report omitted frozen selection")
        return report

    def reserve(self, plan: ResearchPlan) -> ResearchReservation:
        blob, hypothesis_blob = encode_research(plan), encode_research(plan.hypothesis)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, plan.experiment_id)
            if row is not None:
                if row["plan"] != blob:
                    raise ValueError("Immutable research plan changed")
                cached = self._report(row)
                if cached is None:
                    raise ValueError(
                        "Uncertain reserved research run requires explicit investigation"
                    )
                return ResearchReservation(None, cached)
            existing = db.execute(
                "SELECT blob, sha256 FROM research_hypotheses WHERE hypothesis_id=?",
                (plan.hypothesis.hypothesis_id,),
            ).fetchone()
            if existing is not None:
                if _decode(existing[0], existing[1], Hypothesis) != plan.hypothesis:
                    raise ValueError("Immutable hypothesis changed")
            if db.execute("SELECT COUNT(*) FROM research_experiments").fetchone()[0] >= 1000:
                raise ValueError("Research archive capacity exceeded")
            for previous_id in db.execute(
                "SELECT experiment_id FROM research_experiments"
            ).fetchall():
                previous = self._row(db, previous_id[0])
                earlier = _decode(previous["plan"], previous["plan_sha256"], ResearchPlan)
                if _overlaps(plan.development_scope, earlier.holdout_scope):
                    raise ValueError("Development cannot use a reserved holdout period")
                if _overlaps(plan.holdout_scope, earlier.holdout_scope):
                    raise ValueError("Holdout period already assigned to another experiment")
                if _overlaps(plan.holdout_scope, earlier.development_scope):
                    raise ValueError("Holdout period was already exposed as development data")
            token = uuid4().hex
            db.execute(
                "INSERT OR IGNORE INTO research_hypotheses VALUES (?,?,?)",
                (
                    plan.hypothesis.hypothesis_id,
                    hypothesis_blob,
                    sha256(hypothesis_blob).hexdigest(),
                ),
            )
            db.execute(
                "INSERT INTO research_experiments (experiment_id,owner_token,plan,plan_sha256) VALUES (?,?,?,?)",
                (plan.experiment_id, token, blob, sha256(blob).hexdigest()),
            )
            scope = plan.holdout_scope
            db.execute(
                "INSERT INTO research_holdout_claims VALUES (?,?,?,?,?)",
                (
                    plan.experiment_id,
                    scope.instrument_id,
                    _micros(scope.first_observed_at),
                    _micros(scope.last_observed_at),
                    encode_research(scope),
                ),
            )
            db.commit()
            return ResearchReservation(token, None)

    def freeze_selection(self, experiment_id: str, token: str, selection: ResearchSelection):
        blob = encode_research(selection)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, experiment_id)
            if row is None or row["owner_token"] != token or row["report"] is not None:
                raise ValueError("Research selection requires an active owned reservation")
            plan = _decode(row["plan"], row["plan_sha256"], ResearchPlan)
            if selection.development_fingerprint != plan.development_fingerprint:
                raise ValueError("Selection development lineage mismatch")
            if row["selection"] is not None and row["selection"] != blob:
                raise ValueError("Immutable research selection changed")
            db.execute(
                "UPDATE research_experiments SET selection=?,selection_sha256=? WHERE experiment_id=?",
                (blob, sha256(blob).hexdigest(), experiment_id),
            )
            db.commit()

    def finish(self, experiment_id: str, token: str, report: ResearchReport):
        blob = encode_research(report)
        if len(blob) > 16 * 1024 * 1024:
            raise ValueError("Research report capacity exceeded")
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, experiment_id)
            if row is None or row["owner_token"] != token or row["report"] is not None:
                raise ValueError("Research result requires an active owned reservation")
            candidate = dict(row)
            candidate.update(report=blob, report_sha256=sha256(blob).hexdigest())
            self._report(candidate)
            db.execute(
                "UPDATE research_experiments SET report=?,report_sha256=? WHERE experiment_id=?",
                (blob, sha256(blob).hexdigest(), experiment_id),
            )
            db.commit()

    def selection(self, experiment_id: str) -> ResearchSelection | None:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            row = self._row(db, experiment_id)
            return (
                None
                if row is None or row["selection"] is None
                else _decode(row["selection"], row["selection_sha256"], ResearchSelection)
            )

    def get(self, experiment_id: str) -> ResearchReport | None:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            return self._report(self._row(db, experiment_id))

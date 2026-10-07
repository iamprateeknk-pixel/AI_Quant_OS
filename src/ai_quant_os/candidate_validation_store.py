"""Bounded, checked publication of candidate observations with atomic local audit."""

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Lock

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.candidate_prerequisite_failure import (
    CandidatePrerequisiteFailure,
    record_prerequisite_failure,
)
from ai_quant_os.candidate_validation import evaluate_candidate
from ai_quant_os.candidate_validation_contracts import CandidateValidationScorecard

CandidateValidationArtifact = CandidateValidationScorecard | CandidatePrerequisiteFailure


def _identity(artifact):
    if type(artifact) is CandidateValidationScorecard:
        return "SCORECARD", artifact.scorecard_ref
    if type(artifact) is CandidatePrerequisiteFailure:
        return "PREREQUISITE_FAILURE", artifact.failure_ref
    raise ValueError("Typed candidate validation artifact required")


class CandidateValidationStore:
    def __init__(self, path: Path | None = None, *, capacity: int = 32) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 32:
            raise ValueError("Invalid candidate validation capacity")
        existed = path is not None and path.exists()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._capacity, self._lock = capacity, Lock()
        self._db = sqlite3.connect(
            str(path) if path else ":memory:",
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._db.execute("PRAGMA synchronous=FULL")
        self._audit = AuditChain(self._db)
        try:
            with self._transaction():
                tables = {
                    row[0]
                    for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                required = {"candidate_validations", "audit_events", "audit_chain", "audit_head"}
                if existed and (
                    not required <= tables
                    or self._db.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0] != 1
                ):
                    raise ValueError("Candidate validation archive metadata missing")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS candidate_validations (artifact_ref TEXT PRIMARY KEY, kind TEXT NOT NULL, artifact_json TEXT NOT NULL)"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                self._audit.initialize()
                self._verified()
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    @staticmethod
    def _event(artifact):
        kind, reference = _identity(artifact)
        return {
            "action": "CHECKED_CANDIDATE_OBSERVATION_RETAINED",
            "kind": kind,
            "artifact_ref": reference,
            "package_ref": artifact.package_ref,
            "candidate_ref": artifact.candidate_ref,
            "artifact_sha256": sha256(artifact.model_dump_json().encode()).hexdigest(),
        }

    def _verified(self) -> tuple[CandidateValidationArtifact, ...]:
        count, total, maximum = self._db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(artifact_json AS BLOB))),0),COALESCE(MAX(LENGTH(CAST(artifact_json AS BLOB))),0) FROM candidate_validations"
        ).fetchone()
        events, event_bytes = self._db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(CAST(event_json AS BLOB))),0) FROM audit_events"
        ).fetchone()
        if (
            count > self._capacity
            or total > 33554432
            or maximum > 4194304
            or events != count
            or event_bytes > 4096
        ):
            raise ValueError("Candidate validation archive capacity/audit mismatch")
        integrity = self._audit.require_valid()
        if integrity.event_count != count or integrity.legacy_event_count:
            raise ValueError("Candidate validation archive audit mismatch")
        models = {
            "SCORECARD": CandidateValidationScorecard,
            "PREREQUISITE_FAILURE": CandidatePrerequisiteFailure,
        }
        artifacts = []
        for reference, kind, encoded in self._db.execute(
            "SELECT artifact_ref,kind,artifact_json FROM candidate_validations ORDER BY rowid"
        ):
            if kind not in models:
                raise ValueError("Unknown candidate validation artifact kind")
            artifact = models[kind].model_validate_json(encoded)
            if _identity(artifact) != (kind, reference) or artifact.model_dump_json() != encoded:
                raise ValueError("Candidate validation artifact identity mismatch")
            event = self._db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (reference,)
            ).fetchone()
            if event is None or json.loads(event[0]) != self._event(artifact):
                raise ValueError("Candidate validation artifact event binding mismatch")
            artifacts.append(artifact)
        return tuple(artifacts)

    def _retain(self, artifact):
        kind, reference = _identity(artifact)
        encoded = artifact.model_dump_json()
        if len(encoded.encode()) > 4194304:
            raise ValueError("Candidate validation artifact byte bound exceeded")
        with self._lock, self._transaction():
            rows = self._verified()
            for prior in rows:
                if _identity(prior)[1] == reference:
                    if prior != artifact:
                        raise ValueError("Immutable candidate validation artifact conflict")
                    return prior
            if (
                len(rows) >= self._capacity
                or sum(len(row.model_dump_json().encode()) for row in rows) + len(encoded.encode())
                > 33554432
            ):
                raise ValueError("Candidate validation archive capacity exhausted")
            self._db.execute(
                "INSERT INTO candidate_validations VALUES (?,?,?)", (reference, kind, encoded)
            )
            self._audit.append(reference, self._event(artifact))
            return artifact

    def put_evaluation(self, package, tested, holdout, windows, *, policy=None):
        return self._retain(evaluate_candidate(package, tested, holdout, windows, policy=policy))

    def put_failure(self, package, tested) -> CandidatePrerequisiteFailure:
        return self._retain(record_prerequisite_failure(package, tested))

    def get(self, reference: str) -> CandidateValidationArtifact | None:
        with self._lock, self._transaction():
            return next((row for row in self._verified() if _identity(row)[1] == reference), None)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "CandidateValidationStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()

"""Atomic bounded local review proposals; no execution or approval transition."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from threading import Lock

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.incident_agent import WorkflowProposal


class IncidentProposalStore:
    def __init__(self, path: Path | None = None, *, capacity: int = 256):
        if type(capacity) is not int or not 1 <= capacity <= 256:
            raise ValueError("Invalid incident proposal capacity")
        existed = path is not None and path.exists()
        self._capacity, self._lock = capacity, Lock()
        self._db = sqlite3.connect(
            str(path) if path else ":memory:",
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._audit = AuditChain(self._db)
        try:
            with self._transaction():
                tables = {
                    row[0]
                    for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                required = {"incident_proposals", "audit_events", "audit_chain", "audit_head"}
                if existed and (
                    not required <= tables
                    or self._db.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0] != 1
                ):
                    raise ValueError("Incident proposal metadata missing")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS incident_proposals (proposal_ref TEXT PRIMARY KEY, proposal_json TEXT NOT NULL)"
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
    def _event(reference: str) -> dict:
        return {
            "action": "INCIDENT_REVIEW_PROPOSED",
            "proposal_ref": reference,
            "control_authorized": False,
        }

    def _verified(self) -> tuple[WorkflowProposal, ...]:
        integrity = self._audit.require_valid()
        rows = self._db.execute(
            "SELECT proposal_ref,proposal_json FROM incident_proposals ORDER BY proposal_ref"
        ).fetchall()
        if len(rows) != integrity.event_count or len(rows) > 256:
            raise ValueError("Incident proposal audit count mismatch")
        events = dict(self._db.execute("SELECT event_key,event_json FROM audit_events"))
        if set(events) != {reference for reference, _ in rows}:
            raise ValueError("Incident proposal journal binding mismatch")
        proposals = []
        for reference, raw in rows:
            if not isinstance(raw, str) or len(raw.encode()) > 16384:
                raise ValueError("Incident proposal exceeds byte bound")
            proposal = WorkflowProposal.model_validate_json(raw)
            if (
                proposal.model_dump_json() != raw
                or proposal.proposal_ref != reference
                or json.loads(events[reference]) != self._event(reference)
            ):
                raise ValueError("Incident proposal canonical identity mismatch")
            proposals.append(proposal)
        return tuple(proposals)

    def put(self, proposal: WorkflowProposal) -> WorkflowProposal:
        proposal = WorkflowProposal.model_validate_json(proposal.model_dump_json())
        raw = proposal.model_dump_json()
        if len(raw.encode()) > 16384:
            raise ValueError("Incident proposal exceeds byte bound")
        with self._lock, self._transaction():
            rows = self._verified()
            prior = next((row for row in rows if row.proposal_ref == proposal.proposal_ref), None)
            if prior is not None:
                if prior != proposal:
                    raise ValueError("Immutable incident proposal conflict")
                return prior
            if len(rows) >= self._capacity:
                raise ValueError("Incident proposal capacity exhausted")
            self._db.execute(
                "INSERT INTO incident_proposals VALUES (?,?)", (proposal.proposal_ref, raw)
            )
            self._audit.append(proposal.proposal_ref, self._event(proposal.proposal_ref))
            return proposal

    def get(self, reference: str) -> WorkflowProposal | None:
        with self._lock, self._transaction():
            return next((row for row in self._verified() if row.proposal_ref == reference), None)

    def close(self) -> None:
        with self._lock:
            self._db.close()

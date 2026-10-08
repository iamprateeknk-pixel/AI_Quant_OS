"""Independent bounded SQLite incident journal with audit-bound exact source pins."""

import json
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime
from hashlib import sha256
from threading import Lock
from dataclasses import dataclass

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.health_contracts import HealthIncident


class HealthPageChanged(ValueError):
    """The requested timeline belongs to an earlier journal head."""


@dataclass(frozen=True)
class HealthPage:
    incidents: tuple[HealthIncident, ...]
    total: int
    head_hash: str
    offset: int
    next_offset: int | None


def encode_incident(incident):
    data = asdict(incident)
    data["detected_at"] = incident.detected_at.isoformat()
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


class HealthStore:
    def __init__(self, path, *, capacity=1000):
        if type(capacity) is not int or not 1 <= capacity <= 1000:
            raise ValueError("Health capacity must be between 1 and 1000")
        self.capacity = capacity
        self._lock = Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self._audit = AuditChain(self._db)
        try:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                self._audit.initialize()
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS health_incidents (incident_id TEXT PRIMARY KEY, incident_json TEXT NOT NULL, incident_hash TEXT NOT NULL)"
                )
                self._require_valid()
        except BaseException:
            self._db.close()
            raise

    def _require_valid(self):
        integrity = self._audit.require_valid()
        count = self._db.execute("SELECT COUNT(*) FROM health_incidents").fetchone()[0]
        if count != integrity.event_count or count > 1000:
            raise ValueError("Health record/audit integrity count mismatch")
        return integrity

    @staticmethod
    def _event(incident_id, digest):
        return {"action": "HEALTH_INCIDENT", "incident_id": incident_id, "incident_hash": digest}

    def _read(self, incident_id):
        row = self._db.execute(
            "SELECT incident_json, incident_hash FROM health_incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if row is None:
            return None
        raw, digest = row
        if len(raw.encode()) > 32768 or sha256(raw.encode()).hexdigest() != digest:
            raise ValueError("Health record integrity failed")
        try:
            data = json.loads(raw)
            data["detected_at"] = datetime.fromisoformat(data["detected_at"])
            incident = HealthIncident(**data)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError("Health record canonical integrity failed") from error
        if incident.incident_id != incident_id or encode_incident(incident) != raw:
            raise ValueError("Health record canonical identity failed")
        event = self._db.execute(
            "SELECT event_json FROM audit_events WHERE event_key = ?", (f"health:{incident_id}",)
        ).fetchone()
        if event is None or json.loads(event[0]) != self._event(incident_id, digest):
            raise ValueError("Health record audit binding failed")
        return incident

    def record(self, incident):
        raw = encode_incident(incident)
        if len(raw.encode()) > 32768:
            raise ValueError("Serialized health evidence exceeds bound")
        # Reconstruct to validate caller-owned instances before transaction admission.
        HealthIncident(**asdict(incident))
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._require_valid()
            prior = self._read(incident.incident_id)
            if prior is not None:
                if replace(incident, detected_at=prior.detected_at) != prior:
                    raise ValueError("Health incident identity conflict")
                return prior
            if (
                self._db.execute("SELECT COUNT(*) FROM health_incidents").fetchone()[0]
                >= self.capacity
            ):
                raise ValueError("Health journal capacity exhausted")
            digest = sha256(raw.encode()).hexdigest()
            self._db.execute(
                "INSERT INTO health_incidents VALUES (?, ?, ?)", (incident.incident_id, raw, digest)
            )
            self._audit.append(
                f"health:{incident.incident_id}", self._event(incident.incident_id, digest)
            )
            return incident

    def incidents(self):
        with self._lock, self._db:
            self._db.execute("BEGIN")
            self._require_valid()
            ids = self._db.execute(
                "SELECT incident_id FROM health_incidents ORDER BY incident_id"
            ).fetchall()
            return tuple(self._read(row[0]) for row in ids)

    def incident_page(self, *, limit: int, offset: int, head: str | None) -> HealthPage:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 1000
        ):
            raise ValueError("Invalid health page bounds")
        with self._lock, self._db:
            self._db.execute("BEGIN")
            integrity = self._require_valid()
            ids = self._db.execute("SELECT incident_id FROM health_incidents").fetchall()
            # Verify the whole journal, including records outside the requested page.
            incidents = tuple(self._read(row[0]) for row in ids)
            if head is not None and head != integrity.head_hash:
                raise HealthPageChanged("Health timeline changed")
            if offset and head is None:
                raise ValueError("Continued health page requires original head")
            ordered = sorted(
                incidents,
                key=lambda incident: (incident.detected_at, incident.incident_id),
                reverse=True,
            )
            next_offset = offset + limit if offset + limit < len(ordered) else None
            return HealthPage(
                tuple(ordered[offset : offset + limit]),
                len(ordered),
                integrity.head_hash,
                offset,
                next_offset,
            )

    def integrity(self):
        self.incidents()
        with self._lock, self._db:
            self._db.execute("BEGIN")
            return self._require_valid()

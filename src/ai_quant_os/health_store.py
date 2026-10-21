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
                self._db.execute("""CREATE TABLE IF NOT EXISTS health_transitions (
                    transition_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    transition_json TEXT NOT NULL,
                    transition_hash TEXT NOT NULL,
                    UNIQUE(incident_id, transition_id)
                )""")
                self._require_valid()
        except BaseException:
            self._db.close()
            raise

    def _require_valid(self):
        integrity = self._audit.require_valid()
        count = self._db.execute("SELECT COUNT(*) FROM health_incidents").fetchone()[0]
        transitions = self._db.execute("SELECT COUNT(*) FROM health_transitions").fetchone()[0]
        if count + transitions != integrity.event_count or count > 1000 or transitions > 10000:
            raise ValueError("Health record/audit integrity count mismatch")
        for (incident_id,) in self._db.execute(
            "SELECT incident_id FROM health_incidents ORDER BY incident_id"
        ).fetchall():
            self._read(incident_id)
        transition_rows = self._db.execute(
            "SELECT transition_id, incident_id, transition_json, transition_hash "
            "FROM health_transitions ORDER BY incident_id, rowid"
        ).fetchall()
        closed = set()
        sequence = {}
        for transition_id, incident_id, raw, digest in transition_rows:
            if len(raw.encode()) > 4096 or sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("Health transition integrity failed")
            event = json.loads(raw)
            incident_row = self._db.execute(
                "SELECT incident_json FROM health_incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            if incident_row is None:
                raise ValueError("Health transition refers to an unknown incident")
            try:
                incident_data = json.loads(incident_row[0])
                transition_at = datetime.fromisoformat(event["at"])
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError("Health transition timestamp integrity failed") from error
            expected_sequence = sequence.get(incident_id, 0) + 1
            sequence[incident_id] = expected_sequence
            if (
                event.get("transition_id") != transition_id
                or transition_id != f"{incident_id}:{expected_sequence}"
                or event.get("incident_id") != incident_id
                or event.get("action") not in {"CLOSED", "REOPENED"}
                or json.dumps(event, sort_keys=True, separators=(",", ":")) != raw
                or transition_at.utcoffset() is None
                or transition_at.utcoffset().total_seconds() != 0
            ):
                raise ValueError("Health transition canonical integrity failed")
            if event["action"] == "CLOSED":
                if (
                    incident_data.get("code")
                    not in {"QUOTE_STALE", "QUOTE_FUTURE", "QUOTE_UNTRUSTED"}
                    or incident_id in closed
                    or not event.get("command_id")
                    or not event.get("reason")
                ):
                    raise ValueError("Health closure transition policy failed")
                closed.add(incident_id)
            else:
                if (
                    incident_id not in closed
                    or event.get("command_id") is not None
                    or event.get("reason") is not None
                ):
                    raise ValueError("Health reopening transition policy failed")
                closed.remove(incident_id)
            audit = self._db.execute(
                "SELECT event_json FROM audit_events WHERE event_key = ?",
                (f"health-transition:{transition_id}",),
            ).fetchone()
            if audit is None or json.loads(audit[0]) != {
                "action": "HEALTH_TRANSITION",
                "transition_id": transition_id,
                "incident_id": incident_id,
                "transition_hash": digest,
            }:
                raise ValueError("Health transition audit binding failed")
        return integrity

    def _append_transition(self, incident_id, action, *, at, command_id=None, reason=None):
        count = self._db.execute(
            "SELECT COUNT(*) FROM health_transitions WHERE incident_id = ?", (incident_id,)
        ).fetchone()[0]
        transition_id = f"{incident_id}:{count + 1}"
        event = {
            "action": action,
            "at": at.isoformat(),
            "command_id": command_id,
            "incident_id": incident_id,
            "reason": reason,
            "transition_id": transition_id,
        }
        raw = json.dumps(event, sort_keys=True, separators=(",", ":"))
        digest = sha256(raw.encode()).hexdigest()
        self._db.execute(
            "INSERT INTO health_transitions VALUES (?, ?, ?, ?)",
            (transition_id, incident_id, raw, digest),
        )
        self._audit.append(
            f"health-transition:{transition_id}",
            {
                "action": "HEALTH_TRANSITION",
                "transition_id": transition_id,
                "incident_id": incident_id,
                "transition_hash": digest,
            },
        )
        return event

    def transitions(self, incident_id):
        return self.transition_map((incident_id,)).get(incident_id, ())

    def transition_map(self, incident_ids=None):
        with self._lock, self._db:
            self._db.execute("BEGIN")
            self._require_valid()
            requested = None if incident_ids is None else set(incident_ids)
            rows = self._db.execute(
                "SELECT incident_id, transition_json FROM health_transitions ORDER BY rowid"
            ).fetchall()
            result = {}
            for row_incident_id, raw in rows:
                if requested is None or row_incident_id in requested:
                    result.setdefault(row_incident_id, []).append(json.loads(raw))
            return {key: tuple(value) for key, value in result.items()}

    def close_incident(self, incident_id, *, closed_at, command_id, reason):
        if (
            not isinstance(command_id, str)
            or not 1 <= len(command_id) <= 128
            or not isinstance(reason, str)
            or not 1 <= len(reason) <= 256
            or not isinstance(closed_at, datetime)
            or closed_at.utcoffset() is None
            or closed_at.utcoffset().total_seconds() != 0
        ):
            raise ValueError("Invalid health incident closure")
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._require_valid()
            incident = self._read(incident_id)
            if incident is None:
                raise ValueError("Unknown health incident")
            if incident.code not in {"QUOTE_STALE", "QUOTE_FUTURE", "QUOTE_UNTRUSTED"}:
                raise ValueError("Only quote incidents may be closed")
            prior = self._db.execute(
                "SELECT transition_json FROM health_transitions WHERE incident_id = ? ORDER BY rowid DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
            prior_event = json.loads(prior[0]) if prior else None
            if prior_event and prior_event["action"] == "CLOSED":
                if prior_event["command_id"] == command_id and prior_event["reason"] == reason:
                    return prior_event
                raise ValueError("Health incident is already closed")
            for (raw,) in self._db.execute(
                "SELECT transition_json FROM health_transitions"
            ).fetchall():
                prior_command = json.loads(raw)
                if prior_command.get("command_id") == command_id:
                    raise ValueError("Health closure command id is already used")
            return self._append_transition(
                incident_id, "CLOSED", at=closed_at, command_id=command_id, reason=reason
            )

    def open_incidents(self):
        with self._lock, self._db:
            self._db.execute("BEGIN")
            self._require_valid()
            ids = self._db.execute(
                "SELECT incident_id FROM health_incidents ORDER BY incident_id"
            ).fetchall()
            open_rows = []
            for (incident_id,) in ids:
                incident = self._read(incident_id)
                row = self._db.execute(
                    "SELECT transition_json FROM health_transitions WHERE incident_id = ? ORDER BY rowid DESC LIMIT 1",
                    (incident_id,),
                ).fetchone()
                if row is None or json.loads(row[0])["action"] != "CLOSED":
                    open_rows.append(incident)
            return tuple(open_rows)

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
                transition = self._db.execute(
                    "SELECT transition_json FROM health_transitions WHERE incident_id = ? ORDER BY rowid DESC LIMIT 1",
                    (incident.incident_id,),
                ).fetchone()
                if transition and json.loads(transition[0])["action"] == "CLOSED":
                    self._append_transition(
                        incident.incident_id, "REOPENED", at=incident.detected_at
                    )
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

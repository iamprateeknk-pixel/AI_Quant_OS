"""Audited durable lifecycle transitions for exact agent manifest versions."""

import json
import sqlite3
from hashlib import sha256
from threading import Lock

from ai_quant_os.audit_chain import AuditChain

_STATES = {"ACTIVE", "LIMITED", "SHADOW", "QUARANTINED"}
_MAX_EVENTS = 10000
_MAX_EVENT_BYTES = 2048


class AgentLifecycleStore:
    def __init__(self, path):
        self._lock = Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self._audit = AuditChain(self._db)
        try:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events "
                    "(event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                self._audit.initialize()
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS agent_lifecycle_events "
                    "(event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL, event_hash TEXT NOT NULL)"
                )
                self._require_valid()
        except BaseException:
            self._db.close()
            raise

    def _require_valid(self):
        integrity = self._audit.require_valid()
        count = self._db.execute("SELECT COUNT(*) FROM agent_lifecycle_events").fetchone()[0]
        if count != integrity.event_count or count > _MAX_EVENTS:
            raise ValueError("Agent lifecycle audit count mismatch")
        last = {}
        rows = self._db.execute(
            "SELECT event_key, event_json, event_hash FROM agent_lifecycle_events ORDER BY rowid"
        ).fetchall()
        for event_key, raw, digest in rows:
            if len(raw.encode()) > _MAX_EVENT_BYTES or sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("Agent lifecycle record integrity failed")
            try:
                event = json.loads(raw)
                identity = (event["agent_id"], event["agent_version"])
                sequence = last.get(identity, (0, "SHADOW"))[0] + 1
                previous_state = last.get(identity, (0, "SHADOW"))[1]
                canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
            except (TypeError, KeyError, ValueError) as error:
                raise ValueError("Agent lifecycle record canonical integrity failed") from error
            if (
                canonical != raw
                or set(event)
                != {
                    "agent_id",
                    "agent_version",
                    "from_state",
                    "manifest_ref",
                    "schema_version",
                    "sequence",
                    "to_state",
                }
                or not isinstance(event.get("agent_id"), str)
                or not isinstance(event.get("agent_version"), str)
                or type(event.get("sequence")) is not int
                or event.get("schema_version") != "1.0"
                or event.get("sequence") != sequence
                or event.get("from_state") != previous_state
                or event.get("from_state") not in _STATES
                or event.get("to_state") not in _STATES
                or event.get("to_state") == previous_state
                or previous_state == "QUARANTINED"
                or not isinstance(event.get("manifest_ref"), str)
                or len(event["manifest_ref"]) != 79
                or not event_key == f"agent-lifecycle:{identity[0]}:{identity[1]}:{sequence}"
            ):
                raise ValueError("Agent lifecycle transition integrity failed")
            audit = self._db.execute(
                "SELECT event_json FROM audit_events WHERE event_key = ?", (event_key,)
            ).fetchone()
            expected = {
                "action": "AGENT_LIFECYCLE",
                "event_hash": digest,
                "event_key": event_key,
            }
            if audit is None or json.loads(audit[0]) != expected:
                raise ValueError("Agent lifecycle audit binding failed")
            last[identity] = (sequence, event["to_state"])
        return last

    def restore(self, manifests):
        with self._lock, self._db:
            self._db.execute("BEGIN")
            last = self._require_valid()
            by_identity = {(item.agent_id, item.agent_version): item for item in manifests}
            result = {}
            for identity, (_, state) in last.items():
                manifest = by_identity.get(identity)
                if manifest is None:
                    raise ValueError("Agent lifecycle references an unknown manifest version")
                rows = self._db.execute(
                    "SELECT event_json FROM agent_lifecycle_events "
                    "WHERE event_key LIKE ? ORDER BY rowid DESC LIMIT 1",
                    (f"agent-lifecycle:{identity[0]}:{identity[1]}:%",),
                ).fetchone()
                event = json.loads(rows[0])
                if event["manifest_ref"] != manifest.manifest_ref:
                    raise ValueError("Agent lifecycle manifest pin mismatch")
                result[identity] = state
            return result

    def transition(self, manifest, from_state, to_state):
        if (
            from_state not in _STATES
            or to_state not in _STATES
            or from_state == to_state
            or from_state == "QUARANTINED"
        ):
            raise ValueError("Invalid durable agent lifecycle transition")
        identity = (manifest.agent_id, manifest.agent_version)
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            last = self._require_valid()
            sequence, prior_state = last.get(identity, (0, "SHADOW"))
            if prior_state != from_state:
                raise ValueError("Agent lifecycle changed outside the durable journal")
            if sequence >= _MAX_EVENTS:
                raise ValueError("Agent lifecycle journal capacity exhausted")
            event_key = f"agent-lifecycle:{identity[0]}:{identity[1]}:{sequence + 1}"
            event = {
                "agent_id": identity[0],
                "agent_version": identity[1],
                "from_state": from_state,
                "manifest_ref": manifest.manifest_ref,
                "schema_version": "1.0",
                "sequence": sequence + 1,
                "to_state": to_state,
            }
            raw = json.dumps(event, sort_keys=True, separators=(",", ":"))
            digest = sha256(raw.encode()).hexdigest()
            self._db.execute(
                "INSERT INTO agent_lifecycle_events VALUES (?, ?, ?)",
                (event_key, raw, digest),
            )
            self._audit.append(
                event_key,
                {"action": "AGENT_LIFECYCLE", "event_hash": digest, "event_key": event_key},
            )

    def close(self):
        with self._lock:
            self._db.close()

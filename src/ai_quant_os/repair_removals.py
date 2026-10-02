"""Audit-bound local removal intents and exact acknowledgements; no execution certificate."""

import json
import re
import sqlite3
import stat
from pathlib import Path

from ai_quant_os.audit_chain import AuditChain


class RemovalJournal:
    """The trusted caller holds repair_root_lock across journal and daemon operations."""

    def __init__(self, root: Path, *, capacity: int = 1000):
        if type(capacity) is not int or not 1 <= capacity <= 1000:
            raise ValueError("Repair removal capacity must be between 1 and 1000")
        self.capacity = capacity
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError("An existing repair journal root is required")
        path = root / "repair-removals.sqlite3"
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            metadata = None
        existed = metadata is not None
        if metadata is not None:
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > 134217728
                or getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
            ):
                raise ValueError("Repair removal journal file identity invalid")
        self._db = sqlite3.connect(str(path), timeout=5)
        self._audit = AuditChain(self._db)
        try:
            self._db.execute("PRAGMA synchronous=FULL")
            if not existed:
                with self._db:
                    self._db.execute(
                        "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                    )
                    self._audit.initialize()
            self._require_valid()
        except BaseException:
            self._db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._db.close()

    def _require_valid(self):
        try:
            count = self._db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            chain_count = self._db.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0]
            if count > 2000 or chain_count > 2000:
                raise ValueError("Repair removal journal capacity exceeded")
            integrity = self._audit.require_valid()
            if integrity.legacy_event_count != 0:
                raise ValueError("Repair removal journal cannot adopt legacy events")
            owners = {}
            for key, raw in self._db.execute(
                "SELECT event_key,event_json FROM audit_events ORDER BY rowid"
            ):
                if len(raw.encode()) > 32768:
                    raise ValueError("Repair removal event exceeds bound")
                event = json.loads(raw)
                if (
                    not isinstance(event, dict)
                    or set(event)
                    != {"schema_version", "owner", "state", "container_id", "lease_json"}
                    or event["schema_version"] != "1.0"
                    or not isinstance(event["owner"], str)
                    or not re.fullmatch(r"[a-f0-9]{32}", event["owner"])
                    or not isinstance(event["container_id"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", event["container_id"])
                    or event["state"] not in {"INTENT", "REMOVED"}
                    or not isinstance(event["lease_json"], str)
                    or len(event["lease_json"].encode()) > 8192
                    or json.dumps(event, sort_keys=True) != raw
                    or key != self._key(event["owner"], event["state"])
                ):
                    raise ValueError("Repair removal event identity invalid")
                prior = owners.get(event["owner"])
                if event["state"] == "INTENT":
                    if prior is not None:
                        raise ValueError("Repair removal intent duplicated")
                elif prior is None or {**prior, "state": "REMOVED"} != event:
                    raise ValueError("Repair removal acknowledgement lacks exact intent")
                owners[event["owner"]] = event
            return integrity
        except sqlite3.Error as error:
            raise ValueError("Repair removal journal integrity unavailable") from error

    @property
    def event_count(self):
        return self._require_valid().event_count

    @staticmethod
    def _key(owner, state):
        return f"repair-removal:{owner}:{state}"

    def _read(self, owner, state):
        row = self._db.execute(
            "SELECT event_json FROM audit_events WHERE event_key=?", (self._key(owner, state),)
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def intent(self, job, owner: str, container_id: str, *, image_id: str):
        _, raw = job._lease(owner, image_id)
        if not isinstance(container_id, str) or not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise ValueError("Repair removal container identity invalid")
        event = {
            "schema_version": "1.0",
            "owner": owner,
            "state": "INTENT",
            "container_id": container_id,
            "lease_json": raw.decode(),
        }
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._require_valid()
            intent_count = self._db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_key LIKE '%:INTENT'"
            ).fetchone()[0]
            if intent_count >= self.capacity and self._read(owner, "INTENT") is None:
                raise ValueError("Repair removal journal capacity exceeded")
            self._audit.append(self._key(owner, "INTENT"), event)

    def removed(self, job, owner: str, container_id: str):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._require_valid()
            event = self._read(owner, "INTENT")
            if event is None:
                raise ValueError("Repair removal requires a prior intent")
            image_id = json.loads(event["lease_json"])["image_id"]
            _, raw = job._lease(owner, image_id)
            if event["container_id"] != container_id or event["lease_json"] != raw.decode():
                raise ValueError("Repair removal acknowledgement binding failed")
            if self._require_valid().event_count >= 2000 and self._read(owner, "REMOVED") is None:
                raise ValueError("Repair removal journal capacity exceeded")
            self._audit.append(self._key(owner, "REMOVED"), {**event, "state": "REMOVED"})

    def confirmed(self, job, owner: str, *, image_id: str):
        self._require_valid()
        event = self._read(owner, "REMOVED")
        _, raw = job._lease(owner, image_id)
        if event is None or event["lease_json"] != raw.decode():
            return None
        return event["container_id"]

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
            if count > 5000 or chain_count > 5000:
                raise ValueError("Repair removal journal capacity exceeded")
            integrity = self._audit.require_valid()
            if integrity.legacy_event_count != 0:
                raise ValueError("Repair removal journal cannot adopt legacy events")
            owners = {}
            creations = {}
            for key, raw in self._db.execute(
                "SELECT event_key,event_json FROM audit_events ORDER BY rowid"
            ):
                if len(raw.encode()) > 32768:
                    raise ValueError("Repair removal event exceeds bound")
                event = json.loads(raw)
                state = event.get("state") if isinstance(event, dict) else None
                creation = state in ("CREATE_INTENT", "CREATE_ACK")
                if (
                    not isinstance(event, dict)
                    or set(event)
                    != {"schema_version", "owner", "state", "container_id", "lease_json"}
                    | ({"arguments_json"} if creation else set())
                    or event["schema_version"]
                    != ("1.2" if creation else "1.1" if state == "CLEANED" else "1.0")
                    or not isinstance(event["owner"], str)
                    or not re.fullmatch(r"[a-f0-9]{32}", event["owner"])
                    or not isinstance(event["container_id"], str)
                    or not re.fullmatch(
                        r"" if state == "CREATE_INTENT" else r"[a-f0-9]{64}", event["container_id"]
                    )
                    or not isinstance(event["state"], str)
                    or event["state"]
                    not in {"CREATE_INTENT", "CREATE_ACK", "INTENT", "REMOVED", "CLEANED"}
                    or not isinstance(event["lease_json"], str)
                    or len(event["lease_json"].encode()) > 8192
                    or json.dumps(event, sort_keys=True) != raw
                    or key != self._key(event["owner"], event["state"])
                ):
                    raise ValueError("Repair removal event identity invalid")
                if creation:
                    arguments = event["arguments_json"]
                    if not isinstance(arguments, str) or len(arguments.encode()) > 16384:
                        raise ValueError("Repair creation arguments exceed bound")
                    if self._arguments(json.loads(arguments)) != arguments:
                        raise ValueError("Repair creation arguments identity invalid")
                    prior = creations.get(event["owner"])
                    if (
                        event["owner"] in owners
                        or (state == "CREATE_INTENT" and prior is not None)
                        or (
                            state == "CREATE_ACK"
                            and (
                                prior is None
                                or prior["state"] != "CREATE_INTENT"
                                or {**prior, "state": state, "container_id": event["container_id"]}
                                != event
                            )
                        )
                    ):
                        raise ValueError("Repair creation sequence invalid")
                    creations[event["owner"]] = event
                    continue
                prior = owners.get(event["owner"])
                if event["state"] == "INTENT":
                    if prior is not None:
                        raise ValueError("Repair removal intent duplicated")
                    created = creations.get(event["owner"])
                    if created is not None and (
                        created["lease_json"] != event["lease_json"]
                        or created["state"] == "CREATE_ACK"
                        and created["container_id"] != event["container_id"]
                    ):
                        raise ValueError("Repair removal differs from creation identity")
                elif (
                    prior is None
                    or prior["state"] != {"REMOVED": "INTENT", "CLEANED": "REMOVED"}[event["state"]]
                    or {**prior, "state": event["state"], "schema_version": event["schema_version"]}
                    != event
                ):
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

    def _owner_count(self) -> int:
        return self._db.execute(
            "SELECT COUNT(DISTINCT substr(event_key,16,32)) FROM audit_events"
        ).fetchone()[0]

    def _has_owner(self, owner: str) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM audit_events WHERE event_key LIKE ? LIMIT 1",
                (self._key(owner, "") + "%",),
            ).fetchone()
            is not None
        )

    def require_capacity(self) -> None:
        """Called under root ownership before any new container lease or create RPC."""
        self._require_valid()
        if self._owner_count() >= self.capacity:
            raise ValueError("Repair removal journal capacity exceeded")

    @staticmethod
    def _arguments(arguments) -> str:
        if (
            not isinstance(arguments, (list, tuple))
            or not arguments
            or any(not isinstance(value, str) for value in arguments)
        ):
            raise ValueError("Repair creation arguments invalid")
        raw = json.dumps(list(arguments), separators=(",", ":"))
        if len(raw.encode()) > 16384:
            raise ValueError("Repair creation arguments exceed bound")
        return raw

    def create_intent(self, job, owner: str, *, image_id: str, arguments) -> None:
        if job.container_image(owner) != image_id:
            raise ValueError("Repair creation image differs from published lease")
        job.verify_container_lease(owner)
        _, lease = job._lease(owner, image_id)
        event = {
            "schema_version": "1.2",
            "owner": owner,
            "state": "CREATE_INTENT",
            "container_id": "",
            "lease_json": lease.decode(),
            "arguments_json": self._arguments(arguments),
        }
        if len(json.dumps(event, sort_keys=True).encode()) > 32768:
            raise ValueError("Repair creation event exceeds bound")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._require_valid()
            if self._owner_count() >= self.capacity and not self._has_owner(owner):
                raise ValueError("Repair removal journal capacity exceeded")
            if self._has_owner(owner) and self._read(owner, "CREATE_INTENT") is None:
                raise ValueError("Repair creation cannot adopt a legacy removal owner")
            self._audit.append(self._key(owner, "CREATE_INTENT"), event)

    def require_create_request(self, job, owner: str, *, image_id: str, arguments) -> None:
        self._require_valid()
        event = self._read(owner, "CREATE_INTENT")
        if event is None:
            return  # Existing schema 1.0 cleanup has no retrospective creation evidence.
        _, raw = job._lease(owner, image_id)
        if event["lease_json"] != raw.decode() or event["arguments_json"] != self._arguments(
            arguments
        ):
            raise ValueError("Repair creation request binding failed")

    def create_ack(self, job, owner: str, container_id: str, *, image_id: str) -> None:
        if not isinstance(container_id, str) or not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise ValueError("Repair creation acknowledgement identity invalid")
        _, raw = job._lease(owner, image_id)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._require_valid()
            event = self._read(owner, "CREATE_INTENT")
            if event is None or event["lease_json"] != raw.decode():
                raise ValueError("Repair creation acknowledgement lacks exact intent")
            if self._read(owner, "INTENT") is not None and self._read(owner, "CREATE_ACK") is None:
                raise ValueError("Repair creation acknowledgement follows removal")
            self._audit.append(
                self._key(owner, "CREATE_ACK"),
                {**event, "state": "CREATE_ACK", "container_id": container_id},
            )

    def created_identity(self, job, owner: str, *, image_id: str) -> str | None:
        self._require_valid()
        event = self._read(owner, "CREATE_ACK")
        _, raw = job._lease(owner, image_id)
        if event is None:
            return None
        if event["lease_json"] != raw.decode():
            raise ValueError("Repair creation acknowledgement binding failed")
        return event["container_id"]

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
            if self._owner_count() >= self.capacity and not self._has_owner(owner):
                raise ValueError("Repair removal journal capacity exceeded")
            created = self.created_identity(job, owner, image_id=image_id)
            requested = self._read(owner, "CREATE_INTENT")
            if (
                created is not None
                and created != container_id
                or requested is not None
                and requested["lease_json"] != raw.decode()
            ):
                raise ValueError("Repair removal differs from creation identity")
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
            if self._require_valid().event_count >= 5000 and self._read(owner, "REMOVED") is None:
                raise ValueError("Repair removal journal capacity exceeded")
            self._audit.append(self._key(owner, "REMOVED"), {**event, "state": "REMOVED"})

    def confirmed(self, job, owner: str, *, image_id: str):
        self._require_valid()
        event = self._read(owner, "REMOVED")
        _, raw = job._lease(owner, image_id)
        if event is None or event["lease_json"] != raw.decode():
            return None
        if self._read(owner, "CLEANED") is not None:
            self._require_absent(job, owner, image_id, "Repair terminal cleanup paths reappeared")
        return event["container_id"]

    def acknowledged_lease(self, owner: str) -> bytes:
        """Return retained lease bytes only after an audit-bound removal acknowledgement."""
        self._require_valid()
        event = self._read(owner, "REMOVED")
        if event is None:
            raise ValueError("Repair cleanup requires a committed removal acknowledgement")
        return event["lease_json"].encode()

    @property
    def unfinished(self) -> bool:
        self._require_valid()
        return (
            self._db.execute(
                "SELECT 1 FROM audit_events i WHERE (i.event_key LIKE '%:INTENT' OR i.event_key LIKE '%:CREATE_INTENT') "
                "AND NOT EXISTS (SELECT 1 FROM audit_events c "
                "WHERE c.event_key=replace(replace(i.event_key,':CREATE_INTENT',':CLEANED'),':INTENT',':CLEANED')) LIMIT 1"
            ).fetchone()
            is not None
        )

    def cleaned(self, job, owner: str, *, image_id: str) -> None:
        """Commit terminal cleanup only after exact removal and filesystem absence."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self.confirmed(job, owner, image_id=image_id) is None:
                raise ValueError("Repair cleanup lacks exact removal acknowledgement")
            self._require_absent(
                job, owner, image_id, "Repair cleanup terminal record requires absent job and lease"
            )
            event = self._read(owner, "REMOVED")
            self._audit.append(
                self._key(owner, "CLEANED"), {**event, "state": "CLEANED", "schema_version": "1.1"}
            )

    @staticmethod
    def _require_absent(job, owner, image_id, message):
        receipt, _ = job._lease(owner, image_id)
        for path in (job.path, receipt):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raise ValueError(message)

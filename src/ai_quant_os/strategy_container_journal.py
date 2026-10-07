"""Audit-bound pre-create ownership and acknowledgements; perform no Docker action."""

from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat
from uuid import uuid4

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_container_contracts import (
    StrategyContainerOwnership,
    StrategyContainerRecord,
)
from ai_quant_os.strategy_image_content import ordinary_directory, parse_metadata, require_digest
from ai_quant_os.strategy_prefix_jobs import PreparedPrefixJob
from ai_quant_os.candidate_dataset_views import CandidateDatasetInput


class StrategyContainerJournal:
    """Caller holds PreparedPrefixJob.admitted across journal and future daemon RPCs."""

    def __init__(self, root: Path, *, capacity: int = 4096):
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("Invalid strategy ownership journal capacity")
        self.root = ordinary_directory(root)
        self.path = self.root / "strategy-containers.sqlite3"
        self.capacity = capacity
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            self._require_file(metadata)
        existed = metadata is not None
        self._db = sqlite3.connect(str(self.path), timeout=5)
        self._identity = (self.path.stat().st_dev, self.path.stat().st_ino)
        self._audit = AuditChain(self._db)
        try:
            self._db.execute("PRAGMA synchronous=FULL")
            if not existed:
                with self._db:
                    self._db.execute(
                        "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                    )
                    self._audit.initialize()
            self._validated()
        except BaseException:
            self._db.close()
            raise

    @staticmethod
    def _require_file(metadata):
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > 134217728
            or getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            raise ValueError(
                "Strategy ownership journal must be a bounded ordinary single-link file"
            )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._db.close()

    @staticmethod
    def _key(record):
        return f"strategy-container:{record.ownership.owner}:{record.state}"

    @staticmethod
    def _event(record):
        event = record.model_dump(mode="json")
        if len(json.dumps(event, sort_keys=True).encode()) > 8192:
            raise ValueError("Encoded strategy ownership event exceeds bound")
        return event

    def _bound(self, ownership):
        if type(ownership) is not StrategyContainerOwnership:
            raise ValueError("Checked strategy ownership model required")
        checked = StrategyContainerOwnership.model_validate_json(ownership.model_dump_json())
        if Path(checked.job_path).parent != self.root:
            raise ValueError("Strategy ownership belongs to a different root")
        return checked

    @staticmethod
    def _step(prior, record):
        if prior is None:
            if record.state != "CREATE_INTENT":
                raise ValueError("Container history lacks its create intent")
            return
        if record.ownership != prior.ownership:
            raise ValueError("Container history ownership binding changed")
        allowed = {
            "CREATE_INTENT": {"CREATE_ACK", "REMOVE_INTENT"},
            "CREATE_ACK": {"REMOVE_INTENT"},
            "REMOVE_INTENT": {"REMOVED"},
            "REMOVED": set(),
        }
        if record.state not in allowed[prior.state] or (
            prior.container_id and record.container_id != prior.container_id
        ):
            raise ValueError("Container history transition or immutable ID changed")

    def _validated(self):
        try:
            metadata = self.path.lstat()
            self._require_file(metadata)
            if (metadata.st_dev, metadata.st_ino) != self._identity:
                raise ValueError("Strategy ownership journal path identity changed")
            count = self._db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            if (
                count > 16384
                or self._db.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0] > 16384
            ):
                raise ValueError("Strategy ownership history bound exceeded")
            integrity = self._audit.require_valid()
            if integrity.legacy_event_count != 0:
                raise ValueError("Strategy ownership cannot adopt legacy events")
            current = {}
            job_owners = {}
            for key, raw in self._db.execute(
                "SELECT event_key,event_json FROM audit_events ORDER BY rowid"
            ):
                if len(raw.encode()) > 8192:
                    raise ValueError("Strategy ownership event exceeds bound")
                record = StrategyContainerRecord.model_validate(parse_metadata(raw.encode()))
                self._bound(record.ownership)
                if key != self._key(record) or raw != json.dumps(
                    record.model_dump(mode="json"), sort_keys=True
                ):
                    raise ValueError("Strategy ownership event identity/canonical form changed")
                self._step(current.get(record.ownership.owner), record)
                if (
                    job_owners.get(record.ownership.job_path, record.ownership.owner)
                    != record.ownership.owner
                ):
                    raise ValueError("Strategy job has multiple container owners")
                job_owners[record.ownership.job_path] = record.ownership.owner
                current[record.ownership.owner] = record
            if len(current) > 4096:
                raise ValueError("Strategy ownership count bound exceeded")
            return current, count
        except sqlite3.Error as error:
            raise ValueError("Strategy ownership integrity metadata unavailable") from error

    @property
    def event_count(self):
        return self._validated()[1]

    def current(self, ownership):
        ownership = self._bound(ownership)
        record = self._validated()[0].get(ownership.owner)
        if record is not None and record.ownership != ownership:
            raise ValueError("Stored strategy ownership differs from expected binding")
        return record

    def reserve(self, ownership):
        ownership = self._bound(ownership)
        with self._db:
            records, _ = self._validated()
            if (
                ownership.owner in records
                or len(records) >= self.capacity
                or any(
                    record.ownership.job_path == ownership.job_path for record in records.values()
                )
            ):
                raise ValueError(
                    "Strategy ownership already reserved or journal capacity exhausted"
                )
            record = StrategyContainerRecord(
                ownership=ownership, state="CREATE_INTENT", container_id=""
            )
            self._audit.append(self._key(record), self._event(record))
        return record

    def _append(self, ownership, state, container_id):
        ownership = self._bound(ownership)
        if type(container_id) is not str:
            raise ValueError("Immutable container ID required")
        record = StrategyContainerRecord(
            ownership=ownership, state=state, container_id=container_id
        )
        event = self._event(record)
        with self._db:
            records, _ = self._validated()
            prior = records.get(ownership.owner)
            existing = self._db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (self._key(record),)
            ).fetchone()
            if existing is not None:
                if existing[0] != json.dumps(event, sort_keys=True):
                    raise ValueError("Conflicting container acknowledgement")
                return record
            if prior is None:
                raise ValueError("Container ownership intent unavailable")
            self._step(prior, record)
            self._audit.append(self._key(record), event)
        return record

    def acknowledge_create(self, ownership, container_id):
        return self._append(ownership, "CREATE_ACK", container_id)

    def removal_intent(self, ownership, container_id):
        """Caller first independently inspects exact owner/name/image/container ID."""
        return self._append(ownership, "REMOVE_INTENT", container_id)

    def acknowledge_removal(self, ownership, container_id):
        """Caller first obtains an exact owned daemon removal acknowledgement."""
        return self._append(ownership, "REMOVED", container_id)


def reserve_strategy_container(
    job: PreparedPrefixJob, package: CandidateDatasetInput, *, image_id: str, worker_sha256: str
) -> StrategyContainerOwnership:
    require_digest(image_id)
    require_digest(worker_sha256, prefix="")
    if type(job) is not PreparedPrefixJob:
        raise ValueError("Prepared original-package job required")
    with job.admitted(package):
        ownership = StrategyContainerOwnership(
            owner=uuid4().hex,
            job_path=str(job.path),
            job_name=job.path.name,
            invocation_ref=job.invocation_ref,
            stage_sha256=job.stage_sha256,
            image_id=image_id,
            worker_sha256=worker_sha256,
        )
        with StrategyContainerJournal(job.path.parent) as journal:
            if len(journal._validated()[0]) >= journal.capacity:
                raise ValueError("Strategy ownership capacity exhausted")
            sidecar = job.path.parent / (job.path.name + ".container.json")
            with sidecar.open("xb") as stream:
                stream.write(ownership.model_dump_json().encode())
                stream.flush()
                os.fsync(stream.fileno())
            journal.reserve(ownership)
        return ownership


def load_strategy_container_ownership(
    job: PreparedPrefixJob, package: CandidateDatasetInput, *, ownership_sha256: str
) -> StrategyContainerOwnership:
    require_digest(ownership_sha256, prefix="")
    if type(job) is not PreparedPrefixJob:
        raise ValueError("Prepared original-package job required")
    with job.admitted(package):
        raw = read_repair_image_input(job.path.parent / (job.path.name + ".container.json"), 8192)
        if sha256(raw).hexdigest() != ownership_sha256:
            raise ValueError("Container ownership differs from independent pin")
        ownership = StrategyContainerOwnership.model_validate(parse_metadata(raw))
        if (
            ownership.job_path != str(job.path)
            or ownership.job_name != job.path.name
            or ownership.invocation_ref != job.invocation_ref
            or ownership.stage_sha256 != job.stage_sha256
            or raw != ownership.model_dump_json().encode()
        ):
            raise ValueError("Container ownership differs from the original prepared job")
        with StrategyContainerJournal(job.path.parent) as journal:
            if journal.current(ownership) is None:
                raise ValueError("Owned container has no durable create intent; retain sidecar")
        return ownership

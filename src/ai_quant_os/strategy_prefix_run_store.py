"""Bounded audit-bound run reservations; callers hold the cooperative run-root lock."""

import json
from pathlib import Path
import sqlite3
import stat

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.strategy_image_content import parse_metadata, require_digest
from ai_quant_os.strategy_prefix_run_contracts import StrategyPrefixRunManifest, StrategyRunPrefix
from ai_quant_os.candidate_dataset_run_contracts import CandidateDatasetRunManifest


class StrategyPrefixRunStore:
    def __init__(self, root: Path, manifest: StrategyPrefixRunManifest, *, create: bool = False):
        self.path = root / "strategy-run.sqlite3"
        if type(manifest) not in (StrategyPrefixRunManifest, CandidateDatasetRunManifest):
            raise ValueError("Exact supported prefix run manifest required")
        self.manifest = type(manifest).model_validate_json(manifest.model_dump_json())
        if create:
            with self.path.open("xb"):
                pass
        metadata = self.path.lstat()
        self._require_file(metadata)
        self._identity = metadata.st_dev, metadata.st_ino
        self._db = sqlite3.connect(str(self.path), timeout=5)
        self._audit = AuditChain(self._db)
        try:
            self._db.execute("PRAGMA synchronous=FULL")
            if create:
                with self._db:
                    self._db.execute(
                        "CREATE TABLE run_header (singleton INTEGER PRIMARY KEY, header_json TEXT NOT NULL)"
                    )
                    self._db.execute(
                        "INSERT INTO run_header VALUES (1, ?)", (self.manifest.model_dump_json(),)
                    )
                    self._db.execute(
                        "CREATE TABLE run_prefixes (prefix_index INTEGER PRIMARY KEY, record_json TEXT NOT NULL)"
                    )
                    self._db.execute(
                        "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                    )
                    self._audit.initialize()
            self.snapshot()
        except BaseException:
            self._db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._db.close()

    def _require_file(self, metadata):
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > self.manifest.maximum_journal_bytes
            or getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            raise ValueError("Run journal must be a bounded ordinary single-link file")

    def _step(self, latest, record):
        if record.index >= self.manifest.row_count:
            raise ValueError("Run prefix index exceeds original coverage")
        prior = latest.get(record.index)
        if prior is None:
            if (
                record.phase != "RESERVED"
                or record.index != len(latest) + 2
                or any(row.phase != "COMPLETED" for row in latest.values())
                or any(row.job_name == record.job_name for row in latest.values())
            ):
                raise ValueError(
                    "Run reservation must be unique and follow complete prior coverage"
                )
        elif (
            (record.job_name, record.invocation_ref) != (prior.job_name, prior.invocation_ref)
            or (prior.phase, record.phase) not in (("RESERVED", "STAGED"), ("STAGED", "COMPLETED"))
            or (prior.stage_sha256 is not None and record.stage_sha256 != prior.stage_sha256)
        ):
            raise ValueError("Run prefix reservation/phase changed")
        if record.receipt is not None and (
            record.receipt.ownership.image_id != self.manifest.handoff.loaded_image_id
            or record.receipt.ownership.worker_sha256 != self.manifest.image_inputs.worker_sha256
        ):
            raise ValueError("Run receipt image/source pin changed")

    def snapshot(self) -> tuple[dict[int, StrategyRunPrefix], dict | None]:
        try:
            metadata = self.path.lstat()
            self._require_file(metadata)
            if (metadata.st_dev, metadata.st_ino) != self._identity:
                raise ValueError("Run journal file identity changed")
            header = self._db.execute("SELECT singleton,header_json FROM run_header").fetchall()
            if header != [(1, self.manifest.model_dump_json())]:
                raise ValueError("Run journal differs from independently pinned manifest")
            maximum = 3 * (self.manifest.row_count - 2) + 2
            if (
                self._db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] > maximum
                or self._db.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0] > maximum
            ):
                raise ValueError("Run event inventory exceeds bound")
            integrity = self._audit.require_valid()
            if integrity.legacy_event_count:
                raise ValueError("Run cannot adopt legacy audit history")
            latest, final = {}, None
            for key, raw in self._db.execute(
                "SELECT event_key,event_json FROM audit_events ORDER BY rowid"
            ):
                if len(raw.encode()) > 65536:
                    raise ValueError("Run event exceeds byte bound")
                event = parse_metadata(raw.encode())
                if raw != json.dumps(event, sort_keys=True):
                    raise ValueError("Run event canonical form changed")
                if key.startswith("prefix:"):
                    if final is not None:
                        raise ValueError("Run prefix appeared after final replay reservation")
                    record = StrategyRunPrefix.model_validate(event)
                    if key != f"prefix:{record.index}:{record.phase}":
                        raise ValueError("Run prefix event identity changed")
                    self._step(latest, record)
                    latest[record.index] = record
                elif key in ("replay-reserved", "run-completed"):
                    if len(latest) != self.manifest.row_count - 2 or any(
                        row.phase != "COMPLETED" for row in latest.values()
                    ):
                        raise ValueError("Run replay requires complete original prefix coverage")
                    if key == "replay-reserved":
                        if final is not None or event != {"state": "REPLAY_RESERVED"}:
                            raise ValueError("Run replay reservation changed")
                    elif (
                        final != {"state": "REPLAY_RESERVED"}
                        or set(event) != {"state", "report_sha256"}
                        or event["state"] != "COMPLETED"
                    ):
                        raise ValueError("Run final acknowledgement changed")
                    if key == "run-completed":
                        require_digest(event["report_sha256"], prefix="")
                    final = event
                else:
                    raise ValueError("Unknown run audit event")
            stored = dict(self._db.execute("SELECT prefix_index,record_json FROM run_prefixes"))
            if stored != {index: row.model_dump_json() for index, row in latest.items()}:
                raise ValueError("Run current rows differ from audited history")
            return latest, final
        except sqlite3.Error as error:
            raise ValueError("Run integrity metadata missing or corrupt") from error

    def advance(self, record: StrategyRunPrefix) -> None:
        checked = StrategyRunPrefix.model_validate_json(record.model_dump_json())
        latest, final = self.snapshot()
        if latest.get(checked.index) == checked:
            return
        if final is not None:
            raise ValueError("Run replay was already reserved")
        self._step(latest, checked)
        event = checked.model_dump(mode="json")
        if len(json.dumps(event, sort_keys=True).encode()) > 65536:
            raise ValueError("Run prefix event exceeds byte bound")
        with self._db:
            self._audit.append(f"prefix:{checked.index}:{checked.phase}", event)
            self._db.execute(
                "INSERT INTO run_prefixes VALUES (?, ?) ON CONFLICT(prefix_index) DO UPDATE SET record_json=excluded.record_json",
                (checked.index, checked.model_dump_json()),
            )
        self.snapshot()

    def reserve_replay(self) -> None:
        latest, final = self.snapshot()
        if (
            final is not None
            or len(latest) != self.manifest.row_count - 2
            or any(row.phase != "COMPLETED" for row in latest.values())
        ):
            raise ValueError("Complete run required before a single replay reservation")
        with self._db:
            self._audit.append("replay-reserved", {"state": "REPLAY_RESERVED"})
        self.snapshot()

    def complete(self, report_sha256: str) -> None:
        require_digest(report_sha256, prefix="")
        _, final = self.snapshot()
        value = {"state": "COMPLETED", "report_sha256": report_sha256}
        if final == value:
            return
        if final != {"state": "REPLAY_RESERVED"}:
            raise ValueError("Run completion requires its replay reservation")
        with self._db:
            self._audit.append("run-completed", value)
        self.snapshot()

"""Bounded atomic stage journal; the caller owns the validation root lock."""

import json
import sqlite3
import stat

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.candidate_validation_job_contracts import (
    ValidationJobManifest,
    ValidationStageRecord,
)


class ValidationJobStore:
    def __init__(self, root, manifest: ValidationJobManifest, *, create=False):
        self.path = root / "validation-job.sqlite3"
        self.manifest = ValidationJobManifest.model_validate_json(manifest.model_dump_json())
        if create:
            with self.path.open("xb"):
                pass
        self._identity = self._file()
        self._db = sqlite3.connect(self.path, timeout=5)
        self._audit = AuditChain(self._db)
        try:
            self._db.execute("PRAGMA synchronous=FULL")
            if create:
                with self._db:
                    self._db.execute("CREATE TABLE job_header (header_json TEXT NOT NULL)")
                    self._db.execute(
                        "INSERT INTO job_header VALUES (?)", (self.manifest.model_dump_json(),)
                    )
                    self._db.execute(
                        "CREATE TABLE stages (stage_index INTEGER PRIMARY KEY, record_json TEXT NOT NULL)"
                    )
                    self._db.execute(
                        "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                    )
                    self._audit.initialize()
            self.snapshot()
        except BaseException:
            self._db.close()
            raise

    def _file(self):
        observed = self.path.lstat()
        if (
            not stat.S_ISREG(observed.st_mode)
            or stat.S_ISLNK(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_size > 16777216
            or getattr(observed, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            raise ValueError("Bounded ordinary validation journal required")
        return observed.st_dev, observed.st_ino

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._db.close()

    def next_index(self, rows):
        if not rows:
            return 0
        latest = rows[max(rows)]
        if latest.phase == "PAUSED":
            return latest.index
        if latest.phase != "COMPLETED":
            raise ValueError("Uncertain stage requires independently pinned reconciliation")
        if latest.stage == "PUBLICATION":
            return None
        if latest.stage == "TESTS" and latest.test_status != "PROPOSED_TESTS_PASSED_UNATTESTED":
            return self.manifest.stage_count - 1
        return latest.index + 1

    def _step(self, rows, record):
        if record.stage != self.manifest.stage_name(record.index):
            raise ValueError("Stage differs from original declaration")
        prior = rows.get(record.index)
        if prior is None:
            if (
                record.index != self.next_index(rows)
                or record.phase != "RESERVED"
                or record.manifest_sha256 is not None
                or record.attempt != 0
            ):
                raise ValueError("Unique ordered reservation required before dispatch")
        else:
            if (prior.phase, record.phase) not in (
                ("RESERVED", "PREPARED"),
                ("RESERVED", "UNCERTAIN"),
                ("PREPARED", "COMPLETED"),
                ("PREPARED", "UNCERTAIN"),
                ("UNCERTAIN", "COMPLETED"),
                ("PREPARED", "PAUSED"),
                ("PAUSED", "PREPARED"),
            ) or (
                prior.manifest_sha256 is not None
                and prior.manifest_sha256 != record.manifest_sha256
            ):
                raise ValueError("Stage acknowledgement changed or repeated")
            expected_attempt = prior.attempt + int(prior.phase == "PAUSED")
            if record.attempt != expected_attempt:
                raise ValueError("Stage dispatch attempt differs from audited pause history")
        following = {**rows, record.index: record}
        if (
            sum(row.attempt + int(row.phase == "PAUSED") for row in following.values())
            > self.manifest.maximum_cooperative_pauses
        ):
            raise ValueError("Validation job cooperative pause budget exceeded")

    def snapshot(self):
        try:
            if self._file() != self._identity:
                raise ValueError("Validation journal identity changed")
            if self._db.execute("SELECT header_json FROM job_header").fetchall() != [
                (self.manifest.model_dump_json(),)
            ]:
                raise ValueError("Validation journal header differs from independent pin")
            count, maximum = self._db.execute(
                "SELECT COUNT(*), COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
            ).fetchone()
            if (
                count > 4 * self.manifest.stage_count + 2 * self.manifest.maximum_cooperative_pauses
                or maximum > 4096
            ):
                raise ValueError("Validation journal capacity exceeded")
            integrity = self._audit.require_valid()
            if integrity.event_count != count or integrity.legacy_event_count:
                raise ValueError("Validation journal audit inventory changed")
            rows = {}
            for key, raw in self._db.execute(
                "SELECT event_key,event_json FROM audit_events ORDER BY rowid"
            ):
                record = ValidationStageRecord.model_validate(json.loads(raw))
                if key != f"stage:{record.index}:{record.attempt}:{record.phase}":
                    raise ValueError("Validation stage audit identity changed")
                self._step(rows, record)
                rows[record.index] = record
            if dict(self._db.execute("SELECT stage_index,record_json FROM stages")) != {
                i: row.model_dump_json() for i, row in rows.items()
            }:
                raise ValueError("Validation stages differ from audit history")
            return rows
        except sqlite3.Error as error:
            raise ValueError("Validation journal metadata missing or corrupt") from error

    def advance(self, record):
        record = ValidationStageRecord.model_validate_json(record.model_dump_json())
        rows = self.snapshot()
        if rows.get(record.index) == record:
            return record
        self._step(rows, record)
        with self._db:
            self._audit.append(
                f"stage:{record.index}:{record.attempt}:{record.phase}",
                record.model_dump(mode="json"),
            )
            self._db.execute(
                "INSERT INTO stages VALUES (?, ?) ON CONFLICT(stage_index) DO UPDATE SET record_json=excluded.record_json",
                (record.index, record.model_dump_json()),
            )
        self.snapshot()
        return record

    def audit_checkpoint(self):
        rows = self.snapshot()
        return rows, self._audit.require_valid()

    def require_audit_checkpoint(self, record, count, digest):
        self.snapshot()
        retained = self._db.execute(
            "SELECT c.event_hash,e.event_json FROM audit_chain c JOIN audit_events e "
            "ON c.event_key=e.event_key WHERE c.sequence=?",
            (count,),
        ).fetchone()
        if (
            retained is None
            or retained[0] != digest
            or json.loads(retained[1]) != record.model_dump(mode="json")
        ):
            raise ValueError("Interruption differs from its original historical audit checkpoint")

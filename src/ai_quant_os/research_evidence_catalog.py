"""Audited training/holdout stores with isolated raw bytes and checked reads."""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Context, localcontext
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from threading import Lock

from ai_quant_os.research_data_contracts import ResearchDataReceipt, research_dataset_ref
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.contracts import _require_aware
from ai_quant_os.market_data import BarDataset
from ai_quant_os.research_data_parsing import parse_research_csv


def _receipt(
    raw: bytes, source_id: str, resource_class: str, received_at: datetime
) -> ResearchDataReceipt:
    _require_aware(received_at)
    dataset = parse_research_csv(raw)
    digest = sha256(raw).hexdigest()
    with localcontext(Context(prec=28)):
        dataset_fingerprint = dataset.fingerprint
    return ResearchDataReceipt(
        dataset_ref=research_dataset_ref(source_id, digest, resource_class),
        source_id=source_id,
        resource_class=resource_class,
        raw_sha256=digest,
        dataset_fingerprint=dataset_fingerprint,
        instrument_id=dataset.bars[0].instrument_id,
        row_count=len(dataset.bars),
        first_observed_at=dataset.bars[0].observed_at,
        last_observed_at=dataset.bars[-1].observed_at,
        received_at=received_at.astimezone(timezone.utc),
    )


def _overlap(first: ResearchDataReceipt, second: ResearchDataReceipt) -> bool:
    return first.instrument_id == second.instrument_id and (
        first.first_observed_at <= second.last_observed_at
        and second.first_observed_at <= first.last_observed_at
    )


class ResearchEvidenceCatalog:
    """Checked research stores; the PAPER API must use the TRAINING-only mode."""

    def __init__(
        self, path: Path | None = None, *, capacity: int = 64, training_only: bool = False
    ) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise ValueError("Invalid research evidence capacity")
        if type(training_only) is not bool:
            raise ValueError("Invalid research evidence access mode")
        existed = path is not None and path.exists()
        holdout_path = (
            None
            if training_only
            else (
                path.with_name(path.stem + ".holdout" + path.suffix) if path is not None else None
            )
        )
        holdout_existed = holdout_path is not None and holdout_path.exists()
        if holdout_existed and not existed:
            raise ValueError("Research catalog metadata missing")
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._capacity, self._lock = capacity, Lock()
        self._training_only = training_only
        self._db = sqlite3.connect(
            str(path) if path else ":memory:",
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._holdout_db = (
            sqlite3.connect(
                str(holdout_path) if holdout_path else ":memory:",
                check_same_thread=False,
                isolation_level=None,
                timeout=5,
            )
            if not training_only
            else None
        )
        self._db.execute("PRAGMA synchronous=FULL")
        if self._holdout_db is not None:
            self._holdout_db.execute("PRAGMA synchronous=FULL")
        self._audit = AuditChain(self._db)
        self._holdout_audit = AuditChain(self._holdout_db) if self._holdout_db is not None else None
        try:
            self._initialize_store(self._db, existed)
            if self._holdout_db is not None:
                self._initialize_store(self._holdout_db, holdout_existed)
                self._migrate_legacy_holdout()
            with self._transaction():
                self._verified()
        except BaseException:
            self._db.close()
            if self._holdout_db is not None:
                self._holdout_db.close()
            raise

    @staticmethod
    def _initialize_store(db: sqlite3.Connection, existed: bool) -> None:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"research_datasets", "audit_events", "audit_chain", "audit_head"}
        if existed and (
            not required <= tables
            or db.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0] != 1
        ):
            raise ValueError("Research catalog metadata missing")
        db.execute(
            "CREATE TABLE IF NOT EXISTS research_datasets (dataset_ref TEXT PRIMARY KEY, raw_csv BLOB NOT NULL, receipt_json TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS research_dataset_tombstones (dataset_ref TEXT PRIMARY KEY, receipt_json TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
        )
        AuditChain(db).initialize()

    @contextmanager
    def _transaction(self):
        databases = (self._db, self._holdout_db) if self._holdout_db is not None else (self._db,)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            if self._holdout_db is not None:
                self._holdout_db.execute("BEGIN IMMEDIATE")
            yield
            if self._holdout_db is not None:
                self._holdout_db.execute("COMMIT")
            self._db.execute("COMMIT")
        except BaseException:
            for db in reversed(databases):
                if db.in_transaction:
                    db.execute("ROLLBACK")
            raise

    @staticmethod
    def _event(receipt: ResearchDataReceipt) -> dict:
        return {
            "action": "RESEARCH_DATA_CLASSIFIED",
            "dataset_ref": receipt.dataset_ref,
            "receipt_sha256": sha256(receipt.model_dump_json().encode()).hexdigest(),
            "resource_class": receipt.resource_class,
        }

    def _verified_store(
        self,
        db: sqlite3.Connection,
        audit: AuditChain,
        *,
        expected_class: str | None,
    ) -> tuple[tuple[ResearchDataReceipt, bytes], ...]:
        count, total, maximum, metadata = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(raw_csv)),0),COALESCE(MAX(LENGTH(raw_csv)),0),COALESCE(MAX(LENGTH(receipt_json)),0) FROM research_datasets"
        ).fetchone()
        tombstone_count, tombstone_bytes = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(receipt_json)),0) FROM research_dataset_tombstones"
        ).fetchone()
        events, event_bytes = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        if (
            count > 64
            or total > 8388608
            or maximum > 1048576
            or metadata > 4096
            or tombstone_count > 64
            or tombstone_bytes > 4096
            or events != count + tombstone_count
            or event_bytes > 2048
        ):
            raise ValueError("Research catalog capacity/audit count mismatch")
        integrity = audit.require_valid()
        if integrity.event_count != events or integrity.legacy_event_count:
            raise ValueError("Research catalog audit binding mismatch")
        tombstones = {}
        for reference, encoded in db.execute(
            "SELECT dataset_ref,receipt_json FROM research_dataset_tombstones"
        ):
            receipt = ResearchDataReceipt.model_validate_json(encoded)
            if (
                receipt.resource_class != "HOLDOUT"
                or receipt.dataset_ref != reference
                or receipt.model_dump_json() != encoded
            ):
                raise ValueError("Research catalog isolation tombstone mismatch")
            event = db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (reference,)
            ).fetchone()
            if event is None or json.loads(event[0]) != self._event(receipt):
                raise ValueError("Research catalog tombstone audit mismatch")
            tombstones[reference] = receipt
        results = []
        for reference, raw, encoded in db.execute(
            "SELECT dataset_ref,raw_csv,receipt_json FROM research_datasets ORDER BY rowid"
        ):
            receipt = ResearchDataReceipt.model_validate_json(encoded)
            if receipt != _receipt(
                raw, receipt.source_id, receipt.resource_class, receipt.received_at
            ) or (reference != receipt.dataset_ref or receipt.model_dump_json() != encoded):
                raise ValueError("Research catalog source identity mismatch")
            if expected_class is not None and receipt.resource_class != expected_class:
                raise ValueError("Research dataset is stored in the wrong class catalog")
            event = db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (reference,)
            ).fetchone()
            if event is None or json.loads(event[0]) != self._event(receipt):
                raise ValueError("Research catalog event binding mismatch")
            if any(
                prior.resource_class != receipt.resource_class and _overlap(prior, receipt)
                for prior, _ in results
            ):
                raise ValueError("Research classification overlap")
            results.append((receipt, raw))
        return tuple(results)

    def _verified_receipts(
        self,
        db: sqlite3.Connection,
        audit: AuditChain,
        *,
        expected_class: str,
    ) -> tuple[ResearchDataReceipt, ...]:
        """Verify sealed-store metadata without selecting its raw CSV column."""
        count, receipt_bytes, maximum_receipt = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(receipt_json)),0),COALESCE(MAX(LENGTH(receipt_json)),0) FROM research_datasets"
        ).fetchone()
        tombstone_count, tombstone_bytes = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(receipt_json)),0) FROM research_dataset_tombstones"
        ).fetchone()
        events = db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        page_count = db.execute("PRAGMA page_count").fetchone()[0]
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        integrity = audit.require_valid()
        if (
            count + tombstone_count > 64
            or receipt_bytes + tombstone_bytes > 262144
            or maximum_receipt > 4096
            or events != count + tombstone_count
            or integrity.event_count != events
            or integrity.legacy_event_count
            or page_count * page_size > 16777216
        ):
            raise ValueError("Research catalog metadata/audit count mismatch")
        receipts = []
        for reference, encoded in db.execute(
            "SELECT dataset_ref,receipt_json FROM research_datasets ORDER BY rowid"
        ):
            receipt = ResearchDataReceipt.model_validate_json(encoded)
            event = db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (reference,)
            ).fetchone()
            if (
                receipt.resource_class != expected_class
                or receipt.dataset_ref != reference
                or receipt.model_dump_json() != encoded
                or event is None
                or json.loads(event[0]) != self._event(receipt)
            ):
                raise ValueError("Research catalog metadata identity mismatch")
            receipts.append(receipt)
        tombstones = self._legacy_tombstone_receipts() if db is self._db else ()
        if any(
            receipt.resource_class != "HOLDOUT"
            or receipt.dataset_ref not in {row.dataset_ref for row in receipts}
            for receipt in tombstones
        ):
            raise ValueError("Research catalog isolation metadata mismatch")
        return tuple(receipts)

    def _verified(self) -> tuple[tuple[ResearchDataReceipt, bytes], ...]:
        if self._training_only:
            self._require_training_only_layout()
        training = self._verified_store(
            self._db,
            self._audit,
            expected_class="TRAINING",
        )
        if self._training_only:
            if len(training) > 64 or sum(len(raw) for _, raw in training) > 8388608:
                raise ValueError("Research catalog capacity/audit count mismatch")
            if any(
                _overlap(receipt, live)
                for receipt in self._legacy_tombstone_receipts()
                for live, _ in training
            ):
                raise ValueError("Research classification overlap")
            return training
        if self._holdout_db is None or self._holdout_audit is None:
            raise ValueError("HOLDOUT catalog is unavailable")
        holdout = self._verified_store(
            self._holdout_db,
            self._holdout_audit,
            expected_class="HOLDOUT",
        )
        rows = training + holdout
        isolated_by_ref = {receipt.dataset_ref: receipt for receipt, _ in holdout}
        if any(
            isolated_by_ref.get(receipt.dataset_ref) != receipt
            for receipt in self._legacy_tombstone_receipts()
        ):
            raise ValueError("Research catalog isolated holdout is missing")
        if len(rows) > 64 or sum(len(raw) for _, raw in rows) > 8388608:
            raise ValueError("Research catalog capacity/audit count mismatch")
        for receipt in self._legacy_tombstone_receipts():
            if any(_overlap(receipt, live) for live, _ in rows if live.resource_class != "HOLDOUT"):
                raise ValueError("Research classification overlap")
        if any(
            first.resource_class != second.resource_class and _overlap(first, second)
            for index, (first, _) in enumerate(rows)
            for second, _ in rows[index + 1 :]
        ):
            raise ValueError("Research classification overlap")
        return rows

    def _require_training_only_layout(self) -> None:
        """Refuse legacy mixed files using receipt metadata before selecting raw bytes."""
        count = self._db.execute("SELECT COUNT(*) FROM research_datasets").fetchone()[0]
        if count > 64:
            raise ValueError("Research catalog capacity/audit count mismatch")
        for reference, encoded in self._db.execute(
            "SELECT dataset_ref,receipt_json FROM research_datasets ORDER BY rowid"
        ):
            receipt = ResearchDataReceipt.model_validate_json(encoded)
            if receipt.dataset_ref != reference:
                raise ValueError("Research catalog metadata identity mismatch")
            if receipt.resource_class != "TRAINING":
                raise ValueError(
                    "Legacy HOLDOUT rows require offline catalog migration before PAPER startup"
                )

    def _legacy_tombstone_receipts(self) -> tuple[ResearchDataReceipt, ...]:
        return tuple(
            ResearchDataReceipt.model_validate_json(encoded)
            for (encoded,) in self._db.execute(
                "SELECT receipt_json FROM research_dataset_tombstones"
            )
        )

    def _migrate_legacy_holdout(self) -> None:
        with self._transaction():
            legacy = self._verified_store(self._db, self._audit, expected_class=None)
            holdouts = tuple(row for row in legacy if row[0].resource_class == "HOLDOUT")
            if not holdouts:
                return
            isolated = self._verified_store(
                self._holdout_db, self._holdout_audit, expected_class="HOLDOUT"
            )
            isolated_by_ref = {receipt.dataset_ref: (receipt, raw) for receipt, raw in isolated}
            for receipt, raw in holdouts:
                existing = isolated_by_ref.get(receipt.dataset_ref)
                if existing is not None and existing != (receipt, raw):
                    raise ValueError("Conflicting isolated holdout migration")
                if existing is None:
                    self._holdout_db.execute(
                        "INSERT INTO research_datasets VALUES (?,?,?)",
                        (receipt.dataset_ref, raw, receipt.model_dump_json()),
                    )
                    self._holdout_audit.append(receipt.dataset_ref, self._event(receipt))
                    isolated_by_ref[receipt.dataset_ref] = (receipt, raw)
                tombstone = self._db.execute(
                    "SELECT receipt_json FROM research_dataset_tombstones WHERE dataset_ref=?",
                    (receipt.dataset_ref,),
                ).fetchone()
                if tombstone is not None and tombstone[0] != receipt.model_dump_json():
                    raise ValueError("Conflicting legacy holdout tombstone")
                self._db.execute(
                    "INSERT OR IGNORE INTO research_dataset_tombstones VALUES (?,?)",
                    (receipt.dataset_ref, receipt.model_dump_json()),
                )
                self._db.execute(
                    "DELETE FROM research_datasets WHERE dataset_ref=?", (receipt.dataset_ref,)
                )
            self._verified_store(self._db, self._audit, expected_class="TRAINING")
            self._verified_store(self._holdout_db, self._holdout_audit, expected_class="HOLDOUT")

    def publish(
        self, raw: bytes, *, source_id: str, resource_class: str, received_at: datetime
    ) -> ResearchDataReceipt:
        receipt = _receipt(raw, source_id, resource_class, received_at)
        if self._training_only and receipt.resource_class != "TRAINING":
            raise PermissionError("HOLDOUT publication requires the isolated validator service")
        with self._lock, self._transaction():
            rows = self._verified()
            prior = next(
                (record for record, _ in rows if record.dataset_ref == receipt.dataset_ref), None
            )
            if prior is not None:
                return prior
            if any(
                record.resource_class != receipt.resource_class and _overlap(record, receipt)
                for record, _ in rows
            ):
                raise ValueError("Research classification overlap")
            if (
                len(rows) >= self._capacity
                or sum(len(body) for _, body in rows) + len(raw) > 8388608
            ):
                raise ValueError("Research catalog capacity exhausted")
            if receipt.resource_class == "TRAINING":
                target_db, target_audit = self._db, self._audit
            elif self._holdout_db is not None and self._holdout_audit is not None:
                target_db, target_audit = self._holdout_db, self._holdout_audit
            else:
                raise PermissionError("HOLDOUT publication requires the isolated validator service")
            target_db.execute(
                "INSERT INTO research_datasets VALUES (?,?,?)",
                (receipt.dataset_ref, raw, receipt.model_dump_json()),
            )
            target_audit.append(receipt.dataset_ref, self._event(receipt))
            return receipt

    def list(self) -> tuple[ResearchDataReceipt, ...]:
        with self._lock, self._transaction():
            return tuple(record for record, _ in self._verified())

    def training_dataset(self, reference: str) -> tuple[ResearchDataReceipt, bytes]:
        """Return one checked TRAINING snapshot from a training-only catalog."""
        if not self._training_only:
            raise PermissionError("Training snapshots require a TRAINING-only catalog")
        with self._lock, self._transaction():
            row = next(
                (record for record in self._verified() if record[0].dataset_ref == reference), None
            )
            if row is None:
                raise KeyError("TRAINING dataset not found")
            return row

    def validation_pair(
        self, training_ref: str, holdout_ref: str
    ) -> tuple[tuple[ResearchDataReceipt, bytes], tuple[ResearchDataReceipt, bytes]]:
        """Trusted operator preparation only; never registered as an agent read tool."""
        if self._training_only:
            raise PermissionError("HOLDOUT evidence requires the isolated validator service")
        with self._lock, self._transaction():
            rows = {record.dataset_ref: (record, raw) for record, raw in self._verified()}
            training, holdout = rows[training_ref], rows[holdout_ref]
            if (training[0].resource_class, holdout[0].resource_class) != ("TRAINING", "HOLDOUT"):
                raise ValueError("Validation requires exact TRAINING/HOLDOUT classification")
            return training, holdout

    def training(
        self, registry: AgentRegistry, agent_id: str, agent_version: str, *, scope: str
    ) -> tuple[tuple[ResearchDataReceipt, BarDataset], ...]:
        registry.require_tool(agent_id, agent_version, "TRAINING_READ", scope=scope)
        with self._lock, self._transaction():
            training_rows = self._verified_store(self._db, self._audit, expected_class="TRAINING")
            if not self._training_only:
                if self._holdout_db is None or self._holdout_audit is None:
                    raise ValueError("HOLDOUT catalog is unavailable")
                holdout_receipts = self._verified_receipts(
                    self._holdout_db, self._holdout_audit, expected_class="HOLDOUT"
                )
                holdout_by_ref = {receipt.dataset_ref: receipt for receipt in holdout_receipts}
                if any(
                    holdout_by_ref.get(receipt.dataset_ref) != receipt
                    for receipt in self._legacy_tombstone_receipts()
                ):
                    raise ValueError("Research catalog isolated holdout is missing")
                for receipt, _ in training_rows:
                    if any(_overlap(receipt, heldout) for heldout in holdout_receipts):
                        raise ValueError("Research classification overlap")
            elif any(
                _overlap(receipt, live)
                for receipt in self._legacy_tombstone_receipts()
                for live, _ in training_rows
            ):
                raise ValueError("Research classification overlap")
            rows = tuple((receipt, parse_research_csv(raw)) for receipt, raw in training_rows)
        registry.require_tool(agent_id, agent_version, "TRAINING_READ", scope=scope)
        return rows

    def close(self) -> None:
        with self._lock:
            self._db.close()
            if self._holdout_db is not None:
                self._holdout_db.close()

    def __enter__(self) -> "ResearchEvidenceCatalog":
        return self

    def __exit__(self, *args) -> None:
        self.close()


class HoldoutEvidenceStore:
    """Read-only view of one service-owned HOLDOUT catalog database."""

    def __init__(self, path: Path) -> None:
        path = Path(path)
        if not path.is_absolute() or not path.is_file():
            raise FileNotFoundError("HOLDOUT evidence store is unavailable")
        self._path = path
        self._lock = Lock()
        self._db = sqlite3.connect(
            path.as_uri() + "?mode=ro",
            uri=True,
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._db.execute("PRAGMA query_only=ON")
        self._audit = AuditChain(self._db)
        try:
            with self._snapshot():
                self._verified()
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _snapshot(self):
        with self._lock:
            self._db.execute("BEGIN")
            try:
                yield
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def _verified(
        self, db: sqlite3.Connection | None = None
    ) -> tuple[tuple[ResearchDataReceipt, bytes], ...]:
        database = self._db if db is None else db
        audit = self._audit if database is self._db else AuditChain(database)
        tables = {
            row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {
            "research_datasets",
            "research_dataset_tombstones",
            "audit_events",
            "audit_chain",
            "audit_head",
        }
        if not required <= tables:
            raise ValueError("HOLDOUT evidence metadata is incomplete")
        if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("HOLDOUT evidence database integrity failed")
        count, total, maximum, metadata = database.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(raw_csv)),0),COALESCE(MAX(LENGTH(raw_csv)),0),COALESCE(MAX(LENGTH(receipt_json)),0) FROM research_datasets"
        ).fetchone()
        tombstone_count, tombstone_bytes = database.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(receipt_json)),0) FROM research_dataset_tombstones"
        ).fetchone()
        events, event_bytes = database.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        page_count = database.execute("PRAGMA page_count").fetchone()[0]
        page_size = database.execute("PRAGMA page_size").fetchone()[0]
        integrity = audit.require_valid()
        if (
            count > 64
            or total > 8388608
            or maximum > 1048576
            or metadata > 4096
            or tombstone_count != 0
            or tombstone_bytes != 0
            or events != count
            or event_bytes > 2048
            or integrity.event_count != count
            or integrity.legacy_event_count
            or page_count * page_size > 16777216
        ):
            raise ValueError("HOLDOUT evidence capacity or audit mismatch")
        results = []
        for reference, raw, encoded in database.execute(
            "SELECT dataset_ref,raw_csv,receipt_json FROM research_datasets ORDER BY rowid"
        ):
            if type(raw) is not bytes:
                raise ValueError("HOLDOUT evidence bytes are malformed")
            receipt = ResearchDataReceipt.model_validate_json(encoded)
            if (
                receipt.resource_class != "HOLDOUT"
                or receipt != _receipt(raw, receipt.source_id, "HOLDOUT", receipt.received_at)
                or reference != receipt.dataset_ref
                or receipt.model_dump_json() != encoded
            ):
                raise ValueError("HOLDOUT evidence identity mismatch")
            event = database.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (reference,)
            ).fetchone()
            if event is None or json.loads(event[0]) != ResearchEvidenceCatalog._event(receipt):
                raise ValueError("HOLDOUT evidence audit binding mismatch")
            results.append((receipt, raw))
        return tuple(results)

    def list(self) -> tuple[ResearchDataReceipt, ...]:
        with self._snapshot():
            return tuple(receipt for receipt, _ in self._verified())

    def get(self, reference: str) -> tuple[ResearchDataReceipt, bytes] | None:
        with self._snapshot():
            return next((row for row in self._verified() if row[0].dataset_ref == reference), None)

    def publish(
        self,
        raw: bytes,
        *,
        source_id: str,
        received_at: datetime,
        training_receipts: tuple[ResearchDataReceipt, ...],
    ) -> ResearchDataReceipt:
        """Append operator-supplied HOLDOUT bytes to the service-owned audit chain."""
        if type(raw) is not bytes or not 0 < len(raw) <= 1048576:
            raise ValueError("HOLDOUT data exceeds the publication bound")
        if type(training_receipts) is not tuple or len(training_receipts) > 64:
            raise ValueError("TRAINING receipt inventory exceeds the publication bound")
        if any(
            type(receipt) is not ResearchDataReceipt or receipt.resource_class != "TRAINING"
            for receipt in training_receipts
        ) or len({receipt.dataset_ref for receipt in training_receipts}) != len(training_receipts):
            raise ValueError("Checked TRAINING receipts are required")
        receipt = _receipt(raw, source_id, "HOLDOUT", received_at)
        if any(_overlap(receipt, training) for training in training_receipts):
            raise ValueError("Research classification overlap")

        with self._lock:
            metadata = os.lstat(self._path)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("HOLDOUT evidence destination must be a regular file")
            database = sqlite3.connect(str(self._path), isolation_level=None, timeout=5)
            try:
                database.execute("PRAGMA synchronous=FULL")
                database.execute("BEGIN IMMEDIATE")
                rows = self._verified(database)
                prior = next(
                    (
                        existing
                        for existing, _ in rows
                        if existing.dataset_ref == receipt.dataset_ref
                    ),
                    None,
                )
                if prior is not None:
                    database.execute("COMMIT")
                    return prior
                if len(rows) >= 64 or sum(len(body) for _, body in rows) + len(raw) > 8388608:
                    raise ValueError("HOLDOUT evidence capacity exhausted")
                database.execute(
                    "INSERT INTO research_datasets VALUES (?,?,?)",
                    (receipt.dataset_ref, raw, receipt.model_dump_json()),
                )
                AuditChain(database).append(
                    receipt.dataset_ref, ResearchEvidenceCatalog._event(receipt)
                )
                self._verified(database)
                database.execute("COMMIT")
                return receipt
            except BaseException:
                if database.in_transaction:
                    database.execute("ROLLBACK")
                raise
            finally:
                database.close()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "HoldoutEvidenceStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()


def migrate_legacy_holdout_catalog(path: Path) -> tuple[ResearchDataReceipt, ...]:
    """Run the checked legacy split migration before a TRAINING-only API starts."""
    source = Path(path)
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise FileNotFoundError("Legacy research catalog is unavailable")
    with ResearchEvidenceCatalog(source) as catalog:
        return tuple(row for row in catalog.list() if row.resource_class == "HOLDOUT")


def copy_holdout_evidence_store(
    source_path: Path, destination_path: Path
) -> tuple[ResearchDataReceipt, ...]:
    """Stage a verified HOLDOUT snapshot without replacing or deleting either store."""
    source = Path(source_path)
    destination = Path(destination_path)
    source_info = os.lstat(source) if source.exists() or source.is_symlink() else None
    if (
        not source.is_absolute()
        or source_info is None
        or not stat.S_ISREG(source_info.st_mode)
        or getattr(source_info, "st_file_attributes", 0) & 0x400
    ):
        raise FileNotFoundError("HOLDOUT source store is unavailable")
    if not destination.is_absolute() or not destination.parent.is_dir():
        raise ValueError("HOLDOUT destination directory must already exist")
    if destination.parent.is_symlink():
        raise ValueError("HOLDOUT destination directory cannot be a symbolic link")
    destination_info = (
        os.lstat(destination) if destination.exists() or destination.is_symlink() else None
    )
    if destination_info is not None and (
        not stat.S_ISREG(destination_info.st_mode)
        or getattr(destination_info, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError("HOLDOUT destination must be a regular non-reparse file")
    if destination.exists() and os.path.samefile(source, destination):
        raise ValueError("HOLDOUT source and destination must be separate files")
    if os.path.normcase(str(source.resolve())) == os.path.normcase(str(destination.resolve())):
        raise ValueError("HOLDOUT source and destination must differ")

    def checked_rows(path: Path) -> tuple[tuple[ResearchDataReceipt, bytes], ...]:
        with HoldoutEvidenceStore(path) as store, store._snapshot():
            return store._verified()

    if destination.exists() or destination.is_symlink():
        try:
            source_rows = checked_rows(source)
            destination_rows = checked_rows(destination)
        except (OSError, ValueError, sqlite3.Error):
            raise FileExistsError(
                "HOLDOUT destination already exists and was not replaced"
            ) from None
        if destination_rows != source_rows:
            raise FileExistsError("HOLDOUT destination differs from source and was not replaced")
        return tuple(receipt for receipt, _ in source_rows)

    source_rows = checked_rows(source)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".stage", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with HoldoutEvidenceStore(source) as store:
            target_db = sqlite3.connect(temporary, isolation_level=None, timeout=5)
            try:
                store._db.backup(target_db)
            finally:
                target_db.close()
        if checked_rows(temporary) != source_rows:
            raise ValueError("Staged HOLDOUT snapshot differs from its verified source")
        # A hard link gives atomic create-if-absent semantics on the same volume.
        os.link(temporary, destination)
        temporary.unlink()
        return tuple(receipt for receipt, _ in source_rows)
    finally:
        for candidate in (
            temporary,
            Path(str(temporary) + "-journal"),
            Path(str(temporary) + "-wal"),
            Path(str(temporary) + "-shm"),
        ):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass

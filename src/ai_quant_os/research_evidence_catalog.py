"""Audited historical CSV catalog with immutable labels and checked training reads."""

from contextlib import contextmanager
import csv
from datetime import datetime, timezone
from decimal import Context, InvalidOperation, localcontext
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
from threading import Lock

from ai_quant_os.research_data_contracts import ResearchDataReceipt, research_dataset_ref
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.contracts import _require_aware
from ai_quant_os.market_data import BarDataset, import_bars_csv_bytes
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS


def _dataset(raw: bytes) -> BarDataset:
    if type(raw) is not bytes or not 0 < len(raw) <= 1048576:
        raise ValueError("Research CSV byte bound exceeded")
    try:
        rows = csv.reader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True)
        header = next(rows, None)
        if header != ["instrument_id", "observed_at", "open", "high", "low", "close", "volume"]:
            raise ValueError("Unsupported research CSV columns")
        for count, row in enumerate(rows, start=1):
            if count > 4096 or len(row) != 7 or any(not value for value in row):
                raise ValueError("Invalid research CSV row shape or count")
        dataset = import_bars_csv_bytes(raw)
    except (KeyError, TypeError, UnicodeError, csv.Error, InvalidOperation) as error:
        raise ValueError("Invalid research CSV") from error
    if (
        not 2 <= len(dataset.bars) <= 4096
        or dataset.duplicate_rows
        or len({row.instrument_id for row in dataset.bars}) != 1
        or dataset.bars[0].instrument_id not in {row.instrument_id for row in PAPER_INSTRUMENTS}
    ):
        raise ValueError("Research requires unique bounded single-instrument observations")
    return dataset


def _receipt(
    raw: bytes, source_id: str, resource_class: str, received_at: datetime
) -> ResearchDataReceipt:
    _require_aware(received_at)
    dataset = _dataset(raw)
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
    def __init__(self, path: Path | None = None, *, capacity: int = 64) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise ValueError("Invalid research evidence capacity")
        existed = path is not None and path.exists()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._capacity, self._lock = capacity, Lock()
        self._db = sqlite3.connect(
            str(path) if path else ":memory:",
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._db.execute("PRAGMA synchronous=FULL")
        self._audit = AuditChain(self._db)
        try:
            with self._transaction():
                tables = {
                    row[0]
                    for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                required = {"research_datasets", "audit_events", "audit_chain", "audit_head"}
                if existed and (
                    not required <= tables
                    or self._db.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0] != 1
                ):
                    raise ValueError("Research catalog metadata missing")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS research_datasets (dataset_ref TEXT PRIMARY KEY, raw_csv BLOB NOT NULL, receipt_json TEXT NOT NULL)"
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
    def _event(receipt: ResearchDataReceipt) -> dict:
        return {
            "action": "RESEARCH_DATA_CLASSIFIED",
            "dataset_ref": receipt.dataset_ref,
            "receipt_sha256": sha256(receipt.model_dump_json().encode()).hexdigest(),
            "resource_class": receipt.resource_class,
        }

    def _verified(self) -> tuple[tuple[ResearchDataReceipt, bytes], ...]:
        count, total, maximum, metadata = self._db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(raw_csv)),0),COALESCE(MAX(LENGTH(raw_csv)),0),COALESCE(MAX(LENGTH(receipt_json)),0) FROM research_datasets"
        ).fetchone()
        events, event_bytes = self._db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        if (
            count > 64
            or total > 8388608
            or maximum > 1048576
            or metadata > 4096
            or events != count
            or event_bytes > 2048
        ):
            raise ValueError("Research catalog capacity/audit count mismatch")
        integrity = self._audit.require_valid()
        if integrity.event_count != count or integrity.legacy_event_count:
            raise ValueError("Research catalog audit binding mismatch")
        results = []
        for reference, raw, encoded in self._db.execute(
            "SELECT dataset_ref,raw_csv,receipt_json FROM research_datasets ORDER BY rowid"
        ):
            receipt = ResearchDataReceipt.model_validate_json(encoded)
            if receipt != _receipt(
                raw, receipt.source_id, receipt.resource_class, receipt.received_at
            ) or (reference != receipt.dataset_ref or receipt.model_dump_json() != encoded):
                raise ValueError("Research catalog source identity mismatch")
            event = self._db.execute(
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

    def publish(
        self, raw: bytes, *, source_id: str, resource_class: str, received_at: datetime
    ) -> ResearchDataReceipt:
        receipt = _receipt(raw, source_id, resource_class, received_at)
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
            self._db.execute(
                "INSERT INTO research_datasets VALUES (?,?,?)",
                (receipt.dataset_ref, raw, receipt.model_dump_json()),
            )
            self._audit.append(receipt.dataset_ref, self._event(receipt))
            return receipt

    def list(self) -> tuple[ResearchDataReceipt, ...]:
        with self._lock, self._transaction():
            return tuple(record for record, _ in self._verified())

    def training(
        self, registry: AgentRegistry, agent_id: str, agent_version: str, *, scope: str
    ) -> tuple[tuple[ResearchDataReceipt, BarDataset], ...]:
        registry.require_tool(agent_id, agent_version, "TRAINING_READ", scope=scope)
        with self._lock, self._transaction():
            rows = tuple(
                (record, _dataset(raw))
                for record, raw in self._verified()
                if record.resource_class == "TRAINING"
            )
        registry.require_tool(agent_id, agent_version, "TRAINING_READ", scope=scope)
        return rows

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "ResearchEvidenceCatalog":
        return self

    def __exit__(self, *args) -> None:
        self.close()

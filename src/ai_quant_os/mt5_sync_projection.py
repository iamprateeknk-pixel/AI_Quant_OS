"""Retained observational account state and deal revisions from verified gateway receipts."""

import json
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import UUID

from pydantic import UUID4, Field

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.mt5_account_sync import (
    DemoSyncRequest,
    SyncBatch,
    FillView,
    CashEvent,
    account_scope,
)
from ai_quant_os.mt5_demo_observation import DemoCaptureRequest, ObservationModel
from ai_quant_os.mt5_demo_gateway import DemoObservationGateway, canonical, request_identity, utc
from ai_quant_os.mt5_demo_worker import validate_sync

MAX_RECEIPTS = 1000
MAX_VERSIONS = 4096
MAX_PER_DEAL = 128
MAX_MISSING = 128
MAX_RECORD_BYTES = 32768


class ProjectionRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ProjectionRecord(ObservationModel):
    attempt_id: UUID4
    receipt_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    connection_policy_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    batch: SyncBatch


class ProjectionResult(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["DISABLED", "APPLIED", "ALREADY_APPLIED", "WITHHELD"]
    code: (
        Literal[
            "SOURCE_WITHHELD",
            "STORAGE_UNAVAILABLE",
            "ATTEMPT_CONFLICT",
            "FOREIGN_SCOPE",
            "STALE_OBSERVATION",
            "HISTORY_GAP",
            "CAPACITY",
        ]
        | None
    ) = None
    attempt_id: UUID4
    new_deals: int = Field(default=0, ge=0, strict=True)
    revised_deals: int = Field(default=0, ge=0, strict=True)
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False


class DealVersion(ObservationModel):
    event_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=1, strict=True)
    kind: Literal["FILL", "CASH"]
    content_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_attempt_id: UUID4
    source_receipt_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_in_capture: datetime
    payload: FillView | CashEvent
    native_correction_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"


class ProjectionView(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_kind: Literal["ACCOUNT_SYNC_PROJECTION"] = "ACCOUNT_SYNC_PROJECTION"
    latest_attempt_id: UUID4
    latest_receipt_ref: str
    receipts: int = Field(ge=1, strict=True)
    known_deals: int = Field(ge=0, strict=True)
    revision_count: int = Field(ge=0, strict=True)
    history_start: datetime
    history_through: datetime
    missing_prior_deals: tuple[str, ...]
    quote_context: Literal["FRESH", "STALE"]
    batch: SyncBatch
    history_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False


def fold(records):
    versions = {}
    missing = set()
    first = last = None
    start = through = None
    for record in records:
        batch = record.batch
        utc(batch.observed_at)
        utc(batch.history_start)
        utc(batch.history_end)
        if first is None:
            first = record
            start, through = batch.history_start, batch.history_end
        else:
            if (
                record.connection_policy_ref != first.connection_policy_ref
                or batch.account_scope_ref != first.batch.account_scope_ref
            ):
                raise ProjectionRejected("FOREIGN_SCOPE")
            if batch.observed_at <= last.batch.observed_at:
                raise ProjectionRejected("STALE_OBSERVATION")
            if batch.history_start > through or batch.history_end < start:
                raise ProjectionRejected("HISTORY_GAP")
            start = min(start, batch.history_start)
            through = max(through, batch.history_end)
        observed = {d.event_ref for d in (*batch.fills, *batch.cash_events)}
        for ref, history in versions.items():
            time = history[-1].payload.observed_at
            if batch.history_start <= time <= batch.history_end and ref not in observed:
                missing.add(ref)
        missing.difference_update(observed)
        for kind, collection in (("FILL", batch.fills), ("CASH", batch.cash_events)):
            for deal in collection:
                content = sha256(
                    canonical({"kind": kind, "payload": deal.model_dump(mode="json")}).encode()
                ).hexdigest()
                history = versions.setdefault(deal.event_ref, [])
                if not history or history[-1].content_ref != content:
                    history.append(
                        DealVersion(
                            event_ref=deal.event_ref,
                            revision=len(history) + 1,
                            kind=kind,
                            content_ref=content,
                            source_attempt_id=record.attempt_id,
                            source_receipt_ref=record.receipt_ref,
                            observed_in_capture=batch.observed_at,
                            payload=deal,
                        )
                    )
                    if len(history) > MAX_PER_DEAL:
                        raise ProjectionRejected("CAPACITY")
        if sum(len(v) for v in versions.values()) > MAX_VERSIONS or len(missing) > MAX_MISSING:
            raise ProjectionRejected("CAPACITY")
        last = record
    return versions, tuple(sorted(missing)), start, through


class SyncProjectionStore:
    def __init__(self, path: Path, *, clock=lambda: datetime.now(timezone.utc)):
        self.clock = clock
        self._lock = Lock()
        self._connection = None
        connection = None
        existed = path.exists()
        try:
            connection = sqlite3.connect(path, timeout=2, check_same_thread=False)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            tables = {
                r[0]
                for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not tables:
                if existed:
                    raise ValueError("Existing empty projection must not be reset")
                connection.execute(
                    "CREATE TABLE sync_projection_meta (singleton INTEGER PRIMARY KEY, version TEXT NOT NULL)"
                )
                connection.execute("INSERT INTO sync_projection_meta VALUES (1,'1.0')")
                connection.execute(
                    "CREATE TABLE sync_projection_receipts (attempt_id TEXT PRIMARY KEY, receipt_json TEXT NOT NULL, content_ref TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                AuditChain(connection).initialize()
            elif tables != {
                "sync_projection_meta",
                "sync_projection_receipts",
                "audit_events",
                "audit_chain",
                "audit_head",
            }:
                raise ValueError("Unexpected projection layout")
            self._connection = connection
            self._verify()
            connection.commit()
        except Exception:
            if connection is not None:
                connection.rollback()
                connection.close()
            self._connection = None

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _verify(self):
        connection = self._connection
        if connection is None or connection.execute(
            "SELECT * FROM sync_projection_meta"
        ).fetchall() != [(1, "1.0")]:
            raise ValueError("Projection unavailable")
        for table, column in (
            ("sync_projection_receipts", "receipt_json"),
            ("audit_events", "event_json"),
        ):
            if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > MAX_RECEIPTS:
                raise ValueError("Projection capacity exceeded")
            if connection.execute(
                f"SELECT 1 FROM {table} WHERE length({column})>32768 LIMIT 1"
            ).fetchone():
                raise ValueError("Projection payload bound exceeded")
        chain = AuditChain(connection).require_valid()
        if chain.legacy_event_count != 0:
            raise ValueError("Unexpected legacy records")
        records = []
        for key, raw in connection.execute(
            "SELECT e.event_key,e.event_json FROM audit_chain c JOIN audit_events e ON e.event_key=c.event_key ORDER BY c.sequence"
        ):
            event = json.loads(raw)
            if set(event) != {"kind", "record"} or event["kind"] != "SYNC_OBSERVED":
                raise ValueError("Unexpected projection event")
            record = ProjectionRecord.model_validate(event["record"])
            if key != str(record.attempt_id):
                raise ValueError("Projection identity mismatch")
            records.append(record)
        projection = {}
        for attempt, raw, digest in connection.execute("SELECT * FROM sync_projection_receipts"):
            if sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("Projection hash mismatch")
            record = ProjectionRecord.model_validate_json(raw)
            if attempt != str(record.attempt_id):
                raise ValueError("Projection key mismatch")
            projection[attempt] = record
        if projection != {str(r.attempt_id): r for r in records} or len(records) != len(projection):
            raise ValueError("Projection differs from audit history")
        fold(records)
        return records

    def apply(
        self, gateway: DemoObservationGateway, request: DemoSyncRequest, attempt_id: UUID
    ) -> ProjectionResult:
        if not isinstance(attempt_id, UUID) or attempt_id.version != 4:
            raise ValueError("Attempt identity must be UUID4")
        if not request.enabled:
            return ProjectionResult(state="DISABLED", attempt_id=attempt_id)
        with self._lock:
            try:
                if self._connection is None:
                    raise ValueError("Projection unavailable")
                self._connection.execute("BEGIN")
                records = self._verify()
            except Exception:
                return ProjectionResult(
                    state="WITHHELD", code="STORAGE_UNAVAILABLE", attempt_id=attempt_id
                )
            finally:
                if self._connection is not None:
                    self._connection.rollback()
            prior = next((r for r in records if r.attempt_id == attempt_id), None)
        if prior is not None:
            if (
                prior.connection_policy_ref != request_identity(request.connection)
                or prior.batch.history_start != request.history_start
                or prior.batch.history_end != request.history_end
            ):
                return ProjectionResult(
                    state="WITHHELD", code="ATTEMPT_CONFLICT", attempt_id=attempt_id
                )
            commitment = gateway.receipt_reference(request, attempt_id)
            if commitment is None:
                return ProjectionResult(
                    state="WITHHELD", code="SOURCE_WITHHELD", attempt_id=attempt_id
                )
            if commitment != prior.receipt_ref:
                return ProjectionResult(
                    state="WITHHELD", code="ATTEMPT_CONFLICT", attempt_id=attempt_id
                )
            return ProjectionResult(state="ALREADY_APPLIED", attempt_id=attempt_id)
        receipt = gateway.replay_existing(request, attempt_id)
        if (
            receipt.state != "RETURNED"
            or receipt.report is None
            or receipt.report.state != "CAPTURED"
            or receipt.receipt_ref is None
        ):
            return ProjectionResult(state="WITHHELD", code="SOURCE_WITHHELD", attempt_id=attempt_id)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                records = self._verify()
                existing = next((r for r in records if r.attempt_id == attempt_id), None)
                if existing is not None:
                    self._connection.rollback()
                    if existing.receipt_ref != receipt.receipt_ref:
                        return ProjectionResult(
                            state="WITHHELD", code="ATTEMPT_CONFLICT", attempt_id=attempt_id
                        )
                    return ProjectionResult(state="ALREADY_APPLIED", attempt_id=attempt_id)
                if len(records) >= MAX_RECEIPTS:
                    raise ProjectionRejected("CAPACITY")
                validate_sync(receipt.report.model_dump_json().encode(), request, utc(self.clock()))
                record = ProjectionRecord(
                    attempt_id=attempt_id,
                    receipt_ref=receipt.receipt_ref,
                    connection_policy_ref=request_identity(request.connection),
                    batch=receipt.report.batch,
                )
                before = fold(records)[0]
                after = fold([*records, record])[0]
                raw = canonical(record.model_dump(mode="json"))
                if len(raw.encode()) > MAX_RECORD_BYTES:
                    raise ProjectionRejected("CAPACITY")
                self._connection.execute(
                    "INSERT INTO sync_projection_receipts VALUES (?,?,?)",
                    (str(attempt_id), raw, sha256(raw.encode()).hexdigest()),
                )
                AuditChain(self._connection).append(
                    str(attempt_id),
                    {"kind": "SYNC_OBSERVED", "record": record.model_dump(mode="json")},
                )
                self._connection.commit()
                new = len(set(after) - set(before))
                revisions = (
                    sum(len(v) for v in after.values()) - sum(len(v) for v in before.values()) - new
                )
                return ProjectionResult(
                    state="APPLIED", attempt_id=attempt_id, new_deals=new, revised_deals=revisions
                )
            except ProjectionRejected as exc:
                self._connection.rollback()
                return ProjectionResult(state="WITHHELD", code=exc.code, attempt_id=attempt_id)
            except Exception:
                if self._connection is not None:
                    self._connection.rollback()
                return ProjectionResult(
                    state="WITHHELD", code="STORAGE_UNAVAILABLE", attempt_id=attempt_id
                )

    def _read(self, connection: DemoCaptureRequest):
        try:
            if self._connection is None:
                raise ProjectionRejected("STORAGE_UNAVAILABLE")
            self._connection.execute("BEGIN")
            records = self._verify()
            if records and (
                records[0].connection_policy_ref != request_identity(connection)
                or records[0].batch.account_scope_ref != account_scope(connection)
            ):
                raise ProjectionRejected("FOREIGN_SCOPE")
            return records
        except ProjectionRejected:
            raise
        except Exception:
            raise ProjectionRejected("STORAGE_UNAVAILABLE") from None
        finally:
            if self._connection is not None:
                self._connection.rollback()

    def view(self, connection: DemoCaptureRequest) -> ProjectionView | None:
        with self._lock:
            return self._view(self._read(connection))

    def _view(self, records, folded=None):
        if not records:
            return None
        versions, missing, start, through = folded if folded is not None else fold(records)
        last = records[-1]
        now = utc(self.clock())
        return ProjectionView(
            latest_attempt_id=last.attempt_id,
            latest_receipt_ref=last.receipt_ref,
            receipts=len(records),
            known_deals=len(versions),
            revision_count=sum(len(v) for v in versions.values()) - len(versions),
            history_start=start,
            history_through=through,
            missing_prior_deals=missing,
            quote_context="FRESH"
            if last.batch.observed_at <= now < last.batch.valid_until
            else "STALE",
            batch=last.batch,
        )

    def position_history(self, connection: DemoCaptureRequest):
        """Read current positions and latest deal versions from one verified snapshot."""
        with self._lock:
            records = self._read(connection)
            if not records:
                return None, ()
            folded = fold(records)
            latest = tuple(folded[0][ref][-1] for ref in sorted(folded[0]))
            return self._view(records, folded), latest

    def deal_versions(
        self, connection: DemoCaptureRequest, event_ref: str
    ) -> tuple[DealVersion, ...]:
        with self._lock:
            records = self._read(connection)
            return tuple(fold(records)[0].get(event_ref, []))

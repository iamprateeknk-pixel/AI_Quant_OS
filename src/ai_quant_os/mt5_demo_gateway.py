"""Durable at-most-once demo observation admission; no trading route or authority."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

from pydantic import UUID4, Field

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.mt5_demo_observation import DemoCaptureRequest, ObservationModel
from ai_quant_os.mt5_account_sync import DemoSyncRequest
from ai_quant_os.mt5_demo_worker import (
    SupervisedCapture,
    SupervisedSync,
    confirmed_worker_exit,
    inspect_uncertain_worker,
    supervise_demo,
    validate_capture,
)

MAX_ATTEMPTS = 1000
MAX_RESULT = 16384


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def request_identity(request: DemoCaptureRequest | DemoSyncRequest) -> str:
    if isinstance(request, DemoSyncRequest):
        values = [
            "ACCOUNT_SYNC",
            request_identity(request.connection),
            request.history_start.isoformat(),
            request.history_end.isoformat(),
        ]
        return sha256(canonical(values).encode()).hexdigest()
    fields = request.model_dump(mode="json")
    fields.pop("password")
    fields["terminal_path"] = request.terminal_path
    fields["expected_login"] = request.expected_login
    fields["expected_server"] = request.expected_server.get_secret_value()
    return sha256(canonical(fields).encode()).hexdigest()


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC clock required")
    return value


class AttemptRecord(ObservationModel):
    attempt_id: UUID4
    owner_id: UUID4
    worker_id: UUID4
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["RESERVED", "FINISHED", "UNCERTAIN"]
    started_at: datetime
    completed_at: datetime | None = None
    result_json: str | None = Field(default=None, max_length=MAX_RESULT)


class GatewayReply(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["DISABLED", "RETURNED", "WITHHELD"]
    code: (
        Literal[
            "CUSTODY_UNCERTAIN",
            "ATTEMPT_CONFLICT",
            "STORAGE_UNAVAILABLE",
            "STALE_RESULT",
            "CAPACITY",
            "INVALID_RESULT",
        ]
        | None
    ) = None
    attempt_id: UUID4
    receipt_ref: str | None = None
    result: SupervisedCapture | None = None
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    automatic_retry_allowed: Literal[False] = False

    @property
    def report(self):
        return self.result.report if self.result else None


class SyncGatewayReply(GatewayReply):
    observation_kind: Literal["ACCOUNT_SYNC"] = "ACCOUNT_SYNC"
    result: SupervisedSync | None = None


def decode_result(raw: str) -> SupervisedCapture | SupervisedSync:
    data = json.loads(raw)
    model = SupervisedSync if data.get("observation_kind") == "ACCOUNT_SYNC" else SupervisedCapture
    return model.model_validate(data)


class DemoObservationGateway:
    def __init__(
        self, path: Path, *, runner=supervise_demo, clock=lambda: datetime.now(timezone.utc)
    ):
        self.owner_id = uuid4()
        self.runner, self.clock = runner, clock
        self._lock = Lock()
        self._confirmed_exit: UUID | None = None
        self._connection = None
        existed = path.exists()
        connection = None
        try:
            connection = sqlite3.connect(path, timeout=2, check_same_thread=False)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not tables:
                if existed:
                    raise ValueError("Existing empty journal must not be reset")
                connection.execute(
                    "CREATE TABLE demo_gateway_meta (singleton INTEGER PRIMARY KEY, version TEXT NOT NULL)"
                )
                connection.execute("INSERT INTO demo_gateway_meta VALUES (1, '1.0')")
                connection.execute(
                    "CREATE TABLE demo_gateway_attempts (attempt_id TEXT PRIMARY KEY, state TEXT NOT NULL, row_json TEXT NOT NULL, record_hash TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                AuditChain(connection).initialize()
            elif tables != {
                "demo_gateway_meta",
                "demo_gateway_attempts",
                "audit_events",
                "audit_chain",
                "audit_head",
            }:
                raise ValueError("Unexpected journal tables")
            self._connection = connection
            self._verify()
            connection.commit()
        except (sqlite3.Error, ValueError):
            if connection is not None:
                connection.rollback()
                connection.close()
            self._connection = None

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _verify(self) -> dict[str, AttemptRecord]:
        connection = self._connection
        if connection.execute("SELECT * FROM demo_gateway_meta").fetchall() != [(1, "1.0")]:
            raise ValueError("Unsupported journal")
        for table, limit in (
            ("demo_gateway_attempts", MAX_ATTEMPTS),
            ("audit_events", MAX_ATTEMPTS * 3),
        ):
            if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > limit:
                raise ValueError("Journal capacity exceeded")
        if connection.execute(
            "SELECT 1 FROM audit_events WHERE length(event_json) > 32768 LIMIT 1"
        ).fetchone():
            raise ValueError("Audit payload limit")
        if connection.execute(
            "SELECT 1 FROM demo_gateway_attempts WHERE length(row_json) > 32768 LIMIT 1"
        ).fetchone():
            raise ValueError("Projection payload limit")
        chain = AuditChain(connection).require_valid()
        if chain.legacy_event_count != 0:
            raise ValueError("Unverified legacy journal")
        replay = {}
        for key, raw in connection.execute(
            "SELECT event_key, event_json FROM audit_events ORDER BY rowid"
        ):
            event = json.loads(raw)
            if set(event) != {"kind", "row"}:
                raise ValueError("Unexpected event")
            row = AttemptRecord.model_validate(event["row"])
            utc(row.started_at)
            prior = replay.get(str(row.attempt_id))
            kind = event["kind"]
            if key != f"{row.attempt_id}:{kind}":
                raise ValueError("Event identity")
            if kind == "RESERVED":
                if (
                    prior is not None
                    or row.state != "RESERVED"
                    or row.result_json is not None
                    or row.completed_at is not None
                ):
                    raise ValueError("Invalid reservation")
            else:
                if prior is None or kind not in {"FINISHED", "UNCERTAIN", "RECONCILED"}:
                    raise ValueError("Invalid transition")
                before = prior.model_dump(exclude={"state", "completed_at", "result_json"})
                after = row.model_dump(exclude={"state", "completed_at", "result_json"})
                if (
                    before != after
                    or (kind == "RECONCILED" and prior.state not in {"RESERVED", "UNCERTAIN"})
                    or (kind != "RECONCILED" and prior.state != "RESERVED")
                ):
                    raise ValueError("Ownership or transition changed")
                if row.state != ("UNCERTAIN" if kind == "UNCERTAIN" else "FINISHED"):
                    raise ValueError("State mismatch")
                if row.completed_at is None or utc(row.completed_at) < row.started_at:
                    raise ValueError("Invalid completion clock")
                if row.result_json is not None:
                    result = decode_result(row.result_json)
                    if result.worker_id not in (None, row.worker_id):
                        raise ValueError("Worker identity mismatch")
                    if row.state == "FINISHED" and result.worker_custody == "UNKNOWN":
                        raise ValueError("Uncertain completion")
                    if row.state == "UNCERTAIN" and result.worker_custody != "UNKNOWN":
                        raise ValueError("Invalid uncertainty")
                    if kind == "RECONCILED" and (
                        result.state != "WITHHELD"
                        or result.worker_custody != "EXIT_CONFIRMED"
                        or result.code != "WORKER_EXIT"
                    ):
                        raise ValueError("Reconciliation cannot recover observations")
                    self._validate_result_shape(result, row.worker_id)
                elif row.state == "FINISHED":
                    raise ValueError("Missing result")
            replay[str(row.attempt_id)] = row
        projection = {}
        for attempt_id, state, raw, digest in connection.execute(
            "SELECT * FROM demo_gateway_attempts"
        ):
            if len(raw) > 32768 or sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("Projection hash mismatch")
            row = AttemptRecord.model_validate_json(raw)
            if str(row.attempt_id) != attempt_id or row.state != state:
                raise ValueError("Projection identity mismatch")
            projection[attempt_id] = row
        if projection != replay:
            raise ValueError("Projection differs from event history")
        return projection

    def _save(self, row: AttemptRecord, kind: str):
        raw = canonical(row.model_dump(mode="json"))
        self._connection.execute(
            "INSERT OR REPLACE INTO demo_gateway_attempts VALUES (?, ?, ?, ?)",
            (str(row.attempt_id), row.state, raw, sha256(raw.encode()).hexdigest()),
        )
        AuditChain(self._connection).append(
            f"{row.attempt_id}:{kind}", {"kind": kind, "row": row.model_dump(mode="json")}
        )

    @staticmethod
    def _validate_result_shape(result: SupervisedCapture, worker_id: UUID):
        if result.worker_id not in (None, worker_id) or result.state == "DISABLED":
            raise ValueError("Invalid worker response identity")
        if result.worker_custody != "NOT_STARTED" and result.worker_id is None:
            raise ValueError("Missing worker identity")
        if result.state == "RETURNED":
            if (
                result.worker_custody != "EXIT_CONFIRMED"
                or result.report is None
                or result.worker_reply_code != "OK"
                or result.code is not None
            ):
                raise ValueError("Invalid returned observation")
        elif result.report is not None:
            raise ValueError("Withheld response cannot carry observations")
        if result.worker_reply_code not in {
            None,
            "OK",
            "INVALID_REQUEST",
            "TERMINAL_UNAVAILABLE",
            "SDK_UNAVAILABLE",
            "WORKER_FAILURE",
        }:
            raise ValueError("Invalid worker reply code")

    def _reply(
        self, row: AttemptRecord, request: DemoCaptureRequest | DemoSyncRequest
    ) -> GatewayReply:
        reply_model = SyncGatewayReply if isinstance(request, DemoSyncRequest) else GatewayReply
        if row.state != "FINISHED":
            return reply_model(
                state="WITHHELD", code="CUSTODY_UNCERTAIN", attempt_id=row.attempt_id
            )
        result = decode_result(row.result_json)
        if result.report is not None:
            try:
                validate_capture(
                    result.report.model_dump_json().encode(), request, utc(self.clock())
                )
            except Exception:
                return reply_model(state="WITHHELD", code="STALE_RESULT", attempt_id=row.attempt_id)
        return reply_model(
            state=result.state,
            attempt_id=row.attempt_id,
            result=result,
            receipt_ref=sha256(canonical(row.model_dump(mode="json")).encode()).hexdigest(),
        )

    def capture(
        self, request: DemoCaptureRequest | DemoSyncRequest, attempt_id: UUID
    ) -> GatewayReply:
        reply_model = SyncGatewayReply if isinstance(request, DemoSyncRequest) else GatewayReply
        if not isinstance(attempt_id, UUID) or attempt_id.version != 4:
            raise ValueError("Attempt identity must be UUID4")
        if not request.enabled:
            return reply_model(state="DISABLED", attempt_id=attempt_id)
        identity = request_identity(request)
        with self._lock:
            try:
                if self._connection is None:
                    raise ValueError("Journal unavailable")
                self._connection.execute("BEGIN IMMEDIATE")
                rows = self._verify()
                prior = rows.get(str(attempt_id))
                if prior is not None:
                    self._connection.rollback()
                    if prior.request_sha256 != identity:
                        return reply_model(
                            state="WITHHELD", code="ATTEMPT_CONFLICT", attempt_id=attempt_id
                        )
                    return self._reply(prior, request)
                if any(r.state != "FINISHED" for r in rows.values()):
                    self._connection.rollback()
                    return reply_model(
                        state="WITHHELD", code="CUSTODY_UNCERTAIN", attempt_id=attempt_id
                    )
                if len(rows) >= MAX_ATTEMPTS:
                    self._connection.rollback()
                    return reply_model(state="WITHHELD", code="CAPACITY", attempt_id=attempt_id)
                row = AttemptRecord(
                    attempt_id=attempt_id,
                    owner_id=self.owner_id,
                    worker_id=uuid4(),
                    request_sha256=identity,
                    state="RESERVED",
                    started_at=utc(self.clock()),
                )
                self._save(row, "RESERVED")
                self._connection.commit()
            except (sqlite3.Error, ValueError):
                if self._connection is not None:
                    self._connection.rollback()
                return reply_model(
                    state="WITHHELD", code="STORAGE_UNAVAILABLE", attempt_id=attempt_id
                )
        try:
            result = self.runner(request, clock=self.clock, worker_id=row.worker_id)
            if not isinstance(result, SupervisedCapture):
                raise ValueError("Invalid worker response")
            if isinstance(result, SupervisedSync) != isinstance(request, DemoSyncRequest):
                raise ValueError("Wrong observation profile")
            self._validate_result_shape(result, row.worker_id)
            if result.report is not None:
                validate_capture(
                    result.report.model_dump_json().encode(), request, utc(self.clock())
                )
            raw = result.model_dump_json()
            if len(raw.encode()) > MAX_RESULT:
                raise ValueError("Result limit")
        except Exception:
            return reply_model(state="WITHHELD", code="INVALID_RESULT", attempt_id=attempt_id)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                rows = self._verify()
                if rows.get(str(attempt_id)) != row or row.owner_id != self.owner_id:
                    raise ValueError("Reservation changed")
                state = "UNCERTAIN" if result.worker_custody == "UNKNOWN" else "FINISHED"
                completed = row.model_copy(
                    update={"state": state, "completed_at": utc(self.clock()), "result_json": raw}
                )
                if completed.completed_at < row.started_at:
                    raise ValueError("Clock regression")
                self._save(completed, state)
                self._connection.commit()
                return self._reply(completed, request)
            except (sqlite3.Error, ValueError, AttributeError):
                if self._connection is not None:
                    self._connection.rollback()
                return reply_model(
                    state="WITHHELD", code="STORAGE_UNAVAILABLE", attempt_id=attempt_id
                )

    def reconcile(
        self, request: DemoCaptureRequest | DemoSyncRequest, attempt_id: UUID
    ) -> GatewayReply:
        """Resolve only this live owner's exact retained child; never guess from a PID."""
        reply_model = SyncGatewayReply if isinstance(request, DemoSyncRequest) else GatewayReply
        result_model = SupervisedSync if isinstance(request, DemoSyncRequest) else SupervisedCapture
        if not isinstance(attempt_id, UUID) or attempt_id.version != 4:
            raise ValueError("Attempt identity must be UUID4")
        if not request.enabled:
            return reply_model(state="DISABLED", attempt_id=attempt_id)
        with self._lock:
            try:
                if self._connection is None:
                    raise ValueError("Journal unavailable")
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._verify().get(str(attempt_id))
                if row is None or row.request_sha256 != request_identity(request):
                    self._connection.rollback()
                    return reply_model(
                        state="WITHHELD", code="ATTEMPT_CONFLICT", attempt_id=attempt_id
                    )
                if row.state == "FINISHED":
                    self._connection.rollback()
                    return self._reply(row, request)
                if row.owner_id != self.owner_id:
                    self._connection.rollback()
                    return reply_model(
                        state="WITHHELD", code="CUSTODY_UNCERTAIN", attempt_id=attempt_id
                    )
                if self._confirmed_exit != row.worker_id:
                    observed = inspect_uncertain_worker()
                    if (
                        observed.worker_id != row.worker_id
                        or observed.worker_custody != "EXIT_CONFIRMED"
                    ):
                        observed = confirmed_worker_exit(row.worker_id)
                    if (
                        observed.worker_id != row.worker_id
                        or observed.worker_custody != "EXIT_CONFIRMED"
                    ):
                        self._connection.rollback()
                        return reply_model(
                            state="WITHHELD", code="CUSTODY_UNCERTAIN", attempt_id=attempt_id
                        )
                    # Exact child exit cannot reverse. Retain proof until its journal commit succeeds.
                    self._confirmed_exit = row.worker_id
                # Exit proof resolves custody only; the lost native observation is not recovered.
                result = result_model(
                    state="WITHHELD",
                    code="WORKER_EXIT",
                    worker_id=row.worker_id,
                    worker_custody="EXIT_CONFIRMED",
                )
                completed = row.model_copy(
                    update={
                        "state": "FINISHED",
                        "completed_at": utc(self.clock()),
                        "result_json": result.model_dump_json(),
                    }
                )
                if completed.completed_at < row.started_at:
                    raise ValueError("Clock regression")
                self._save(completed, "RECONCILED")
                self._connection.commit()
                self._confirmed_exit = None
                return self._reply(completed, request)
            except (sqlite3.Error, ValueError):
                if self._connection is not None:
                    self._connection.rollback()
                return reply_model(
                    state="WITHHELD", code="STORAGE_UNAVAILABLE", attempt_id=attempt_id
                )

    def custody_status(self) -> Literal["ALL_FINISHED", "CUSTODY_UNCERTAIN", "STORAGE_UNAVAILABLE"]:
        """Inspect retained worker receipts without reserving, launching or reconciling."""
        with self._lock:
            try:
                if self._connection is None:
                    return "STORAGE_UNAVAILABLE"
                self._connection.execute("BEGIN")
                rows = self._verify()
                return (
                    "ALL_FINISHED"
                    if all(row.state == "FINISHED" for row in rows.values())
                    else "CUSTODY_UNCERTAIN"
                )
            except (sqlite3.Error, ValueError):
                return "STORAGE_UNAVAILABLE"
            finally:
                if self._connection is not None:
                    self._connection.rollback()

    def attempt_status(
        self, attempt_id: UUID
    ) -> Literal["ABSENT", "PRESENT", "STORAGE_UNAVAILABLE"]:
        """Check a proposed identity against verified retained intent without dispatch."""
        if not isinstance(attempt_id, UUID) or attempt_id.version != 4:
            raise ValueError("Attempt identity must be UUID4")
        with self._lock:
            try:
                if self._connection is None:
                    return "STORAGE_UNAVAILABLE"
                self._connection.execute("BEGIN")
                return "PRESENT" if str(attempt_id) in self._verify() else "ABSENT"
            except (sqlite3.Error, ValueError):
                return "STORAGE_UNAVAILABLE"
            finally:
                if self._connection is not None:
                    self._connection.rollback()

    def replay_existing(
        self, request: DemoCaptureRequest | DemoSyncRequest, attempt_id: UUID
    ) -> GatewayReply:
        """Read only an existing verified receipt; never reserve or dispatch a missing attempt."""
        reply_model = SyncGatewayReply if isinstance(request, DemoSyncRequest) else GatewayReply
        if not isinstance(attempt_id, UUID) or attempt_id.version != 4:
            raise ValueError("Attempt identity must be UUID4")
        if not request.enabled:
            return reply_model(state="DISABLED", attempt_id=attempt_id)
        with self._lock:
            try:
                if self._connection is None:
                    raise ValueError("Journal unavailable")
                self._connection.execute("BEGIN")
                row = self._verify().get(str(attempt_id))
                if row is None or row.request_sha256 != request_identity(request):
                    self._connection.rollback()
                    return reply_model(
                        state="WITHHELD", code="ATTEMPT_CONFLICT", attempt_id=attempt_id
                    )
                result = self._reply(row, request)
                self._connection.rollback()
                return result
            except (sqlite3.Error, ValueError):
                if self._connection is not None:
                    self._connection.rollback()
                return reply_model(
                    state="WITHHELD", code="STORAGE_UNAVAILABLE", attempt_id=attempt_id
                )

    def receipt_reference(
        self, request: DemoCaptureRequest | DemoSyncRequest, attempt_id: UUID
    ) -> str | None:
        """Read an immutable finished-receipt commitment without projecting expired facts."""
        if not isinstance(attempt_id, UUID) or attempt_id.version != 4:
            raise ValueError("Attempt identity must be UUID4")
        if not request.enabled:
            return None
        with self._lock:
            try:
                if self._connection is None:
                    return None
                self._connection.execute("BEGIN")
                row = self._verify().get(str(attempt_id))
                if (
                    row is None
                    or row.state != "FINISHED"
                    or row.request_sha256 != request_identity(request)
                ):
                    return None
                return sha256(canonical(row.model_dump(mode="json")).encode()).hexdigest()
            except (sqlite3.Error, ValueError):
                return None
            finally:
                if self._connection is not None:
                    self._connection.rollback()

"""Durable due-work controller for explicitly enabled demo observations."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

from pydantic import UUID4, Field, model_validator

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.mt5_account_sync import DemoSyncRequest
from ai_quant_os.mt5_demo_gateway import DemoObservationGateway, canonical, request_identity, utc
from ai_quant_os.mt5_demo_observation import DemoCaptureRequest, ObservationModel
from ai_quant_os.mt5_sync_projection import SyncProjectionStore

MAX_TRANSITIONS = 3000


class SyncSchedule(ObservationModel):
    enabled: bool = Field(default=False, strict=True)
    initial_history_start: datetime
    interval_seconds: int = Field(default=10, ge=5, le=3600, strict=True)
    overlap_seconds: int = Field(default=60, ge=1, le=3600, strict=True)
    max_backoff_seconds: int = Field(default=300, ge=5, le=3600, strict=True)

    @model_validator(mode="after")
    def validate_schedule(self):
        utc(self.initial_history_start)
        if self.max_backoff_seconds < self.interval_seconds:
            raise ValueError("Backoff must cover the capture interval")
        return self


class SyncHealth(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_kind: Literal["DEMO_SYNC_HEALTH"] = "DEMO_SYNC_HEALTH"
    state: Literal["DISABLED", "STARTING", "ACTIVE", "HEALTHY", "DEGRADED", "HALTED"]
    code: (
        Literal[
            "STORAGE_UNAVAILABLE",
            "CLOCK_REGRESSION",
            "HISTORY_GAP",
            "CUSTODY_UNCERTAIN",
            "SOURCE_FAILURE",
            "PROJECTION_FAILURE",
            "CAPACITY",
        ]
        | None
    ) = None
    revision: int = Field(default=0, ge=0, le=MAX_TRANSITIONS, strict=True)
    observed_at: datetime
    next_due_at: datetime
    last_history_end: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0, le=MAX_TRANSITIONS, strict=True)
    pending_attempt_id: UUID4 | None = None
    pending_history_start: datetime | None = None
    pending_history_end: datetime | None = None
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def consistent_pending(self):
        for value in (
            self.observed_at,
            self.next_due_at,
            self.last_history_end,
            self.last_success_at,
            self.pending_history_start,
            self.pending_history_end,
        ):
            if value is not None:
                utc(value)
        pending = (self.pending_attempt_id, self.pending_history_start, self.pending_history_end)
        if any(v is not None for v in pending) != all(v is not None for v in pending):
            raise ValueError("Incomplete pending capture")
        if self.state == "ACTIVE" and self.pending_attempt_id is None:
            raise ValueError("Active capture requires durable intent")
        if self.pending_attempt_id is not None:
            if self.state not in {"ACTIVE", "HALTED"}:
                raise ValueError("Pending capture cannot be discarded")
            if (
                not timedelta(0)
                < self.pending_history_end - self.pending_history_start
                <= timedelta(days=1)
            ):
                raise ValueError("Invalid pending history interval")
        return self


class SyncRecoveryRequest(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    action: Literal["FINISH_PENDING_PROJECTION"] = "FINISH_PENDING_PROJECTION"
    expected_revision: int = Field(ge=1, le=MAX_TRANSITIONS, strict=True)
    pending_attempt_id: UUID4


class SyncRecoveryResult(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["RECOVERED", "WITHHELD", "DISABLED"]
    code: (
        Literal[
            "BUSY",
            "STORAGE_UNAVAILABLE",
            "STATE_CONFLICT",
            "CLOCK_REGRESSION",
            "CAPACITY",
            "SOURCE_WITHHELD",
            "PROJECTION_WITHHELD",
        ]
        | None
    ) = None
    request: SyncRecoveryRequest
    health: SyncHealth | None = None
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"


class SyncClockRecoveryRequest(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    action: Literal["RESUME_CLOCK_HALT"] = "RESUME_CLOCK_HALT"
    expected_revision: int = Field(ge=1, le=MAX_TRANSITIONS, strict=True)


class SyncClockRecoveryResult(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["RESUMED", "WITHHELD", "DISABLED"]
    code: (
        Literal[
            "BUSY",
            "STORAGE_UNAVAILABLE",
            "STATE_CONFLICT",
            "CLOCK_REGRESSION",
            "CAPACITY",
            "HISTORY_GAP",
            "CUSTODY_UNCERTAIN",
            "PROJECTION_WITHHELD",
        ]
        | None
    ) = None
    request: SyncClockRecoveryRequest
    health: SyncHealth | None = None
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"


class SyncBackfillRequest(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    action: Literal["BACKFILL_HISTORY"] = "BACKFILL_HISTORY"
    expected_revision: int = Field(ge=1, le=MAX_TRANSITIONS, strict=True)
    attempt_id: UUID4


class SyncBackfillResult(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["COMPLETED", "WITHHELD", "DISABLED"]
    code: (
        Literal[
            "BUSY",
            "STORAGE_UNAVAILABLE",
            "STATE_CONFLICT",
            "CLOCK_REGRESSION",
            "CAPACITY",
            "ATTEMPT_CONFLICT",
            "CUSTODY_UNCERTAIN",
            "SOURCE_FAILURE",
            "PROJECTION_FAILURE",
            "HISTORY_GAP",
        ]
        | None
    ) = None
    request: SyncBackfillRequest
    health: SyncHealth | None = None
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"


class SyncWindowReductionRequest(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    action: Literal["REDUCE_FAILED_HISTORY_WINDOW"] = "REDUCE_FAILED_HISTORY_WINDOW"
    expected_revision: int = Field(ge=1, le=MAX_TRANSITIONS, strict=True)
    prior_attempt_id: UUID4
    attempt_id: UUID4
    window_seconds: int = Field(ge=1, le=86400, strict=True)

    @model_validator(mode="after")
    def different_attempt(self):
        if self.prior_attempt_id == self.attempt_id:
            raise ValueError("Replacement intent must use a distinct identity")
        return self


class SyncWindowReductionEvidence(ObservationModel):
    request: SyncWindowReductionRequest
    prior_receipt_ref: str = Field(pattern=r"^[a-f0-9]{64}$")


class SyncWindowReductionResult(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["COMPLETED", "WITHHELD", "DISABLED"]
    code: (
        Literal[
            "BUSY",
            "STORAGE_UNAVAILABLE",
            "STATE_CONFLICT",
            "CLOCK_REGRESSION",
            "CAPACITY",
            "ATTEMPT_CONFLICT",
            "CUSTODY_UNCERTAIN",
            "SOURCE_FAILURE",
            "PROJECTION_FAILURE",
            "HISTORY_GAP",
            "WINDOW_NOT_SMALLER",
            "WINDOW_NO_PROGRESS",
            "RECEIPT_INELIGIBLE",
        ]
        | None
    ) = None
    request: SyncWindowReductionRequest
    prior_receipt_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    health: SyncHealth | None = None
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"


class DemoSyncController:
    """Call tick from a service timer; each call performs at most one fixed worker capture."""

    def __init__(
        self,
        path: Path,
        connection: DemoCaptureRequest,
        schedule: SyncSchedule,
        gateway: DemoObservationGateway,
        projection: SyncProjectionStore,
        *,
        clock=lambda: datetime.now(timezone.utc),
    ):
        self.connection, self.schedule = connection, schedule
        self.gateway, self.projection, self.clock = gateway, projection, clock
        self._lock = Lock()
        self._dispatch_lock = Lock()
        self._db = None
        self._policy = sha256(
            canonical([request_identity(connection), schedule.model_dump(mode="json")]).encode()
        ).hexdigest()
        existed = path.exists()
        db = None
        try:
            db = sqlite3.connect(path, timeout=2, check_same_thread=False)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self._db = db
            if not tables:
                if existed:
                    raise ValueError("Existing empty controller cannot be reset")
                db.execute(
                    "CREATE TABLE sync_controller (singleton INTEGER PRIMARY KEY, version TEXT NOT NULL, policy_ref TEXT NOT NULL, state_json TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                AuditChain(db).initialize()
                now = utc(clock())
                state = SyncHealth(state="STARTING", observed_at=now, next_due_at=now)
                raw = canonical(state.model_dump(mode="json"))
                db.execute("INSERT INTO sync_controller VALUES (1,'1.0',?,?)", (self._policy, raw))
                AuditChain(db).append(
                    "0", {"policy_ref": self._policy, "state": state.model_dump(mode="json")}
                )
            elif tables != {"sync_controller", "audit_events", "audit_chain", "audit_head"}:
                raise ValueError("Unexpected controller layout")
            self._read()
            db.commit()
        except Exception:
            if db is not None:
                db.rollback()
                db.close()
            self._db = None

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def _read(self):
        if self._db is None:
            raise ValueError("Controller unavailable")
        row = self._db.execute("SELECT * FROM sync_controller").fetchall()
        if len(row) != 1 or row[0][:3] != (1, "1.0", self._policy) or len(row[0][3]) > 8192:
            raise ValueError("Controller policy or projection mismatch")
        if (
            self._db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            > MAX_TRANSITIONS + 1
        ):
            raise ValueError("Controller capacity")
        if self._db.execute(
            "SELECT 1 FROM audit_events WHERE length(event_json)>8192 LIMIT 1"
        ).fetchone():
            raise ValueError("Controller payload bound")
        chain = AuditChain(self._db).require_valid()
        if chain.legacy_event_count:
            raise ValueError("Unexpected legacy controller")
        prior = None
        for key, raw in self._db.execute(
            "SELECT e.event_key,e.event_json FROM audit_chain c JOIN audit_events e ON e.event_key=c.event_key ORDER BY c.sequence"
        ):
            event = json.loads(raw)
            if (
                set(event)
                not in (
                    {"policy_ref", "state"},
                    {"policy_ref", "state", "recovery"},
                    {"policy_ref", "state", "clock_recovery"},
                    {"policy_ref", "state", "backfill"},
                    {"policy_ref", "state", "window_reduction"},
                )
                or event["policy_ref"] != self._policy
            ):
                raise ValueError("Controller event mismatch")
            state = SyncHealth.model_validate(event["state"])
            if key != str(state.revision) or state.revision != (prior.revision + 1 if prior else 0):
                raise ValueError("Controller sequence mismatch")
            if prior and (
                state.observed_at < prior.observed_at
                or (
                    prior.last_history_end
                    and (
                        state.last_history_end is None
                        or state.last_history_end < prior.last_history_end
                    )
                )
            ):
                raise ValueError("Controller progress regression")
            if "recovery" in event:
                recovery = SyncRecoveryRequest.model_validate(event["recovery"])
                if (
                    prior is None
                    or prior.state != "HALTED"
                    or prior.revision != recovery.expected_revision
                    or prior.pending_attempt_id != recovery.pending_attempt_id
                    or state.state != "HEALTHY"
                    or state.code is not None
                    or state.pending_attempt_id is not None
                    or state.last_history_end != prior.pending_history_end
                    or state.last_success_at != state.observed_at
                    or state.consecutive_failures != 0
                    or state.next_due_at
                    != state.observed_at + timedelta(seconds=self.schedule.interval_seconds)
                ):
                    raise ValueError("Invalid recovery transition")
            if "clock_recovery" in event:
                recovery = SyncClockRecoveryRequest.model_validate(event["clock_recovery"])
                if (
                    prior is None
                    or prior.state != "HALTED"
                    or prior.code != "CLOCK_REGRESSION"
                    or prior.pending_attempt_id is not None
                    or prior.revision != recovery.expected_revision
                    or state.state != "STARTING"
                    or state.code is not None
                    or state.pending_attempt_id is not None
                    or state.last_history_end != prior.last_history_end
                    or state.last_success_at != prior.last_success_at
                    or state.consecutive_failures != prior.consecutive_failures
                    or state.next_due_at != max(state.observed_at, prior.next_due_at)
                ):
                    raise ValueError("Invalid clock recovery transition")
            if "backfill" in event:
                backfill = SyncBackfillRequest.model_validate(event["backfill"])
                start = self._history_start(prior) if prior is not None else None
                if (
                    prior is None
                    or prior.state != "HALTED"
                    or prior.code != "HISTORY_GAP"
                    or prior.pending_attempt_id is not None
                    or prior.revision != backfill.expected_revision
                    or state.state != "ACTIVE"
                    or state.code is not None
                    or state.pending_attempt_id != backfill.attempt_id
                    or state.pending_history_start != start
                    or state.pending_history_end
                    != min(state.observed_at, start + timedelta(days=1))
                    or state.last_history_end != prior.last_history_end
                    or state.last_success_at != prior.last_success_at
                    or state.consecutive_failures != prior.consecutive_failures
                    or state.next_due_at != prior.next_due_at
                ):
                    raise ValueError("Invalid backfill intent transition")
            if "window_reduction" in event:
                evidence = SyncWindowReductionEvidence.model_validate(event["window_reduction"])
                reduction = evidence.request
                if (
                    prior is None
                    or prior.state != "HALTED"
                    or prior.code != "SOURCE_FAILURE"
                    or prior.pending_attempt_id != reduction.prior_attempt_id
                    or prior.revision != reduction.expected_revision
                    or state.state != "ACTIVE"
                    or state.code is not None
                    or state.pending_attempt_id != reduction.attempt_id
                    or state.pending_history_start != prior.pending_history_start
                    or state.pending_history_end
                    != prior.pending_history_start + timedelta(seconds=reduction.window_seconds)
                    or state.pending_history_end >= prior.pending_history_end
                    or (
                        prior.last_history_end is not None
                        and state.pending_history_end <= prior.last_history_end
                    )
                    or state.last_history_end != prior.last_history_end
                    or state.last_success_at != prior.last_success_at
                    or state.consecutive_failures != prior.consecutive_failures
                    or state.next_due_at != prior.next_due_at
                ):
                    raise ValueError("Invalid reduced-window intent transition")
            prior = state
        if prior is None or canonical(prior.model_dump(mode="json")) != row[0][3]:
            raise ValueError("Controller differs from audit history")
        return prior

    def _save(
        self,
        old,
        *,
        recovery=None,
        clock_recovery=None,
        backfill=None,
        window_reduction=None,
        **changes,
    ):
        if old.revision >= MAX_TRANSITIONS:
            raise ValueError("Controller transition capacity")
        state = SyncHealth.model_validate(
            {**old.model_dump(), **changes, "revision": old.revision + 1}
        )
        self._db.execute(
            "UPDATE sync_controller SET state_json=? WHERE singleton=1",
            (canonical(state.model_dump(mode="json")),),
        )
        AuditChain(self._db).append(
            str(state.revision),
            {
                "policy_ref": self._policy,
                "state": state.model_dump(mode="json"),
                **({"recovery": recovery.model_dump(mode="json")} if recovery else {}),
                **({"backfill": backfill.model_dump(mode="json")} if backfill else {}),
                **(
                    {"window_reduction": window_reduction.model_dump(mode="json")}
                    if window_reduction
                    else {}
                ),
                **(
                    {"clock_recovery": clock_recovery.model_dump(mode="json")}
                    if clock_recovery
                    else {}
                ),
            },
        )
        return state

    def _history_start(self, state):
        start = self.schedule.initial_history_start
        if state.last_history_end is not None:
            start = max(
                start, state.last_history_end - timedelta(seconds=self.schedule.overlap_seconds)
            )
        return start

    def _projection_matches_cursor(self, state):
        try:
            view = self.projection.view(self.connection)
            return (view is None and state.last_history_end is None) or (
                view is not None and view.batch.history_end == state.last_history_end
            )
        except Exception:
            return False

    def _unavailable(self, now):
        return SyncHealth(
            state="HALTED", code="STORAGE_UNAVAILABLE", observed_at=now, next_due_at=now
        )

    def recover_pending(self, request: SyncRecoveryRequest) -> SyncRecoveryResult:
        """Finish only an explicitly selected halted receipt; never dispatch a worker."""
        request = SyncRecoveryRequest.model_validate(request.model_dump())
        now = utc(self.clock())

        def reply(code=None, health=None, state="WITHHELD"):
            return SyncRecoveryResult(state=state, code=code, request=request, health=health)

        if not self.schedule.enabled or not self.connection.enabled:
            return reply(state="DISABLED")
        if not self._dispatch_lock.acquire(blocking=False):
            return reply("BUSY")
        try:
            with self._lock:
                try:
                    if self._db is None:
                        raise ValueError("Controller unavailable")
                    self._db.execute("BEGIN IMMEDIATE")
                    current = self._read()
                    if (
                        current.state != "HALTED"
                        or current.revision != request.expected_revision
                        or current.pending_attempt_id != request.pending_attempt_id
                    ):
                        return reply("STATE_CONFLICT", current)
                    if now < current.observed_at:
                        return reply("CLOCK_REGRESSION", current)
                    if current.revision >= MAX_TRANSITIONS - 2:
                        return reply("CAPACITY", current)
                    capture = DemoSyncRequest(
                        connection=self.connection,
                        history_start=current.pending_history_start,
                        history_end=current.pending_history_end,
                    )
                    projected = self.projection.apply(
                        self.gateway, capture, request.pending_attempt_id
                    )
                    if projected.state not in {"APPLIED", "ALREADY_APPLIED"}:
                        return reply(
                            "SOURCE_WITHHELD"
                            if projected.code == "SOURCE_WITHHELD"
                            else "PROJECTION_WITHHELD",
                            current,
                        )
                    recovered = self._save(
                        current,
                        recovery=request,
                        state="HEALTHY",
                        code=None,
                        observed_at=now,
                        next_due_at=now + timedelta(seconds=self.schedule.interval_seconds),
                        last_history_end=current.pending_history_end,
                        last_success_at=now,
                        consecutive_failures=0,
                        pending_attempt_id=None,
                        pending_history_start=None,
                        pending_history_end=None,
                    )
                    self._db.commit()
                    return reply(health=recovered, state="RECOVERED")
                except Exception:
                    return reply("STORAGE_UNAVAILABLE")
                finally:
                    if self._db is not None:
                        self._db.rollback()
        finally:
            self._dispatch_lock.release()

    def recover_clock(self, request: SyncClockRecoveryRequest) -> SyncClockRecoveryResult:
        """Resume a clock-only halt without capturing or changing observed history."""
        request = SyncClockRecoveryRequest.model_validate(request.model_dump())
        now = utc(self.clock())

        def reply(code=None, health=None, state="WITHHELD"):
            return SyncClockRecoveryResult(state=state, code=code, request=request, health=health)

        if not self.schedule.enabled or not self.connection.enabled:
            return reply(state="DISABLED")
        if not self._dispatch_lock.acquire(blocking=False):
            return reply("BUSY")
        try:
            with self._lock:
                try:
                    if self._db is None:
                        raise ValueError("Controller unavailable")
                    self._db.execute("BEGIN IMMEDIATE")
                    current = self._read()
                    if (
                        current.state != "HALTED"
                        or current.code != "CLOCK_REGRESSION"
                        or current.pending_attempt_id is not None
                        or current.revision != request.expected_revision
                    ):
                        return reply("STATE_CONFLICT", current)
                    if now < current.observed_at:
                        return reply("CLOCK_REGRESSION", current)
                    if current.revision >= MAX_TRANSITIONS - 2:
                        return reply("CAPACITY", current)
                    start = self._history_start(current)
                    if not timedelta(0) < now - start <= timedelta(days=1):
                        return reply("HISTORY_GAP", current)
                    custody = self.gateway.custody_status()
                    if custody != "ALL_FINISHED":
                        return reply(custody, current)
                    if not self._projection_matches_cursor(current):
                        return reply("PROJECTION_WITHHELD", current)
                    resumed = self._save(
                        current,
                        clock_recovery=request,
                        state="STARTING",
                        code=None,
                        observed_at=now,
                        next_due_at=max(now, current.next_due_at),
                    )
                    self._db.commit()
                    return reply(health=resumed, state="RESUMED")
                except Exception:
                    return reply("STORAGE_UNAVAILABLE")
                finally:
                    if self._db is not None:
                        self._db.rollback()
        finally:
            self._dispatch_lock.release()

    def backfill_history(self, request: SyncBackfillRequest) -> SyncBackfillResult:
        """Capture one oldest bounded history window under an explicit durable intent."""
        request = SyncBackfillRequest.model_validate(request.model_dump())
        now = utc(self.clock())

        def reply(code=None, health=None, state="WITHHELD"):
            return SyncBackfillResult(state=state, code=code, request=request, health=health)

        if not self.schedule.enabled or not self.connection.enabled:
            return reply(state="DISABLED")
        if not self._dispatch_lock.acquire(blocking=False):
            return reply("BUSY")
        try:
            with self._lock:
                try:
                    if self._db is None:
                        raise ValueError("Controller unavailable")
                    self._db.execute("BEGIN IMMEDIATE")
                    current = self._read()
                    if (
                        current.state != "HALTED"
                        or current.code != "HISTORY_GAP"
                        or current.pending_attempt_id is not None
                        or current.revision != request.expected_revision
                    ):
                        return reply("STATE_CONFLICT", current)
                    if now < current.observed_at:
                        return reply("CLOCK_REGRESSION", current)
                    if current.revision >= MAX_TRANSITIONS - 2:
                        return reply("CAPACITY", current)
                    custody = self.gateway.custody_status()
                    if custody != "ALL_FINISHED":
                        return reply(custody, current)
                    identity = self.gateway.attempt_status(request.attempt_id)
                    if identity != "ABSENT":
                        return reply(
                            "ATTEMPT_CONFLICT" if identity == "PRESENT" else identity, current
                        )
                    start = self._history_start(current)
                    end = min(now, start + timedelta(days=1))
                    if end <= start:
                        return reply("HISTORY_GAP", current)
                    if not self._projection_matches_cursor(current):
                        return reply("PROJECTION_FAILURE", current)
                    pending = self._save(
                        current,
                        backfill=request,
                        state="ACTIVE",
                        code=None,
                        observed_at=now,
                        pending_attempt_id=request.attempt_id,
                        pending_history_start=start,
                        pending_history_end=end,
                    )
                    self._db.commit()
                except Exception:
                    return reply("STORAGE_UNAVAILABLE")
                finally:
                    if self._db is not None:
                        self._db.rollback()
            try:
                outcome = self._capture_pending(pending)
                return reply(
                    code=outcome.code,
                    health=outcome,
                    state="COMPLETED" if outcome.state == "HEALTHY" else "WITHHELD",
                )
            except Exception:
                return reply("STORAGE_UNAVAILABLE")
        finally:
            self._dispatch_lock.release()

    def reduce_failed_window(
        self, request: SyncWindowReductionRequest
    ) -> SyncWindowReductionResult:
        """Replace only a finished capacity failure with a smaller oldest window."""
        request = SyncWindowReductionRequest.model_validate(request.model_dump())
        now = utc(self.clock())
        receipt_ref = None

        def reply(code=None, health=None, state="WITHHELD"):
            return SyncWindowReductionResult(
                state=state,
                code=code,
                request=request,
                health=health,
                prior_receipt_ref=receipt_ref,
            )

        if not self.schedule.enabled or not self.connection.enabled:
            return reply(state="DISABLED")
        if not self._dispatch_lock.acquire(blocking=False):
            return reply("BUSY")
        try:
            with self._lock:
                try:
                    if self._db is None:
                        raise ValueError("Controller unavailable")
                    self._db.execute("BEGIN IMMEDIATE")
                    current = self._read()
                    if (
                        current.state != "HALTED"
                        or current.code != "SOURCE_FAILURE"
                        or current.pending_attempt_id != request.prior_attempt_id
                        or current.revision != request.expected_revision
                    ):
                        return reply("STATE_CONFLICT", current)
                    if now < current.observed_at:
                        return reply("CLOCK_REGRESSION", current)
                    if current.revision >= MAX_TRANSITIONS - 2:
                        return reply("CAPACITY", current)
                    end = current.pending_history_start + timedelta(seconds=request.window_seconds)
                    if end >= current.pending_history_end:
                        return reply("WINDOW_NOT_SMALLER", current)
                    if current.last_history_end is not None and end <= current.last_history_end:
                        return reply("WINDOW_NO_PROGRESS", current)
                    custody = self.gateway.custody_status()
                    if custody != "ALL_FINISHED":
                        return reply(custody, current)
                    identity = self.gateway.attempt_status(request.attempt_id)
                    if identity != "ABSENT":
                        return reply(
                            "ATTEMPT_CONFLICT" if identity == "PRESENT" else identity, current
                        )
                    old_request = DemoSyncRequest(
                        connection=self.connection,
                        history_start=current.pending_history_start,
                        history_end=current.pending_history_end,
                    )
                    old = self.gateway.replay_existing(old_request, request.prior_attempt_id)
                    if (
                        old.receipt_ref is None
                        or old.result is None
                        or old.result.worker_custody != "EXIT_CONFIRMED"
                        or old.report is None
                        or old.report.state != "WITHHELD"
                        or old.report.code not in {"CAPACITY", "RESULT_LIMIT"}
                    ):
                        return reply("RECEIPT_INELIGIBLE", current)
                    receipt_ref = old.receipt_ref
                    if not self._projection_matches_cursor(current):
                        return reply("PROJECTION_FAILURE", current)
                    pending = self._save(
                        current,
                        window_reduction=SyncWindowReductionEvidence(
                            request=request,
                            prior_receipt_ref=receipt_ref,
                        ),
                        state="ACTIVE",
                        code=None,
                        observed_at=now,
                        pending_attempt_id=request.attempt_id,
                        pending_history_end=end,
                    )
                    self._db.commit()
                except Exception:
                    return reply("STORAGE_UNAVAILABLE")
                finally:
                    if self._db is not None:
                        self._db.rollback()
            try:
                outcome = self._capture_pending(pending)
                return reply(
                    code=outcome.code,
                    health=outcome,
                    state="COMPLETED" if outcome.state == "HEALTHY" else "WITHHELD",
                )
            except Exception:
                return reply("STORAGE_UNAVAILABLE")
        finally:
            self._dispatch_lock.release()

    def tick(self) -> SyncHealth:
        now = utc(self.clock())
        if not self.schedule.enabled or not self.connection.enabled:
            return SyncHealth(state="DISABLED", observed_at=now, next_due_at=now)
        if not self._dispatch_lock.acquire(blocking=False):
            with self._lock:
                try:
                    if self._db is None:
                        raise ValueError("Controller unavailable")
                    self._db.execute("BEGIN")
                    return self._read()
                except Exception:
                    return self._unavailable(now)
                finally:
                    if self._db is not None:
                        self._db.rollback()
        try:
            return self._tick_due(now)
        finally:
            self._dispatch_lock.release()

    def _tick_due(self, now):
        with self._lock:
            try:
                if self._db is None:
                    raise ValueError("Controller unavailable")
                self._db.execute("BEGIN IMMEDIATE")
                state = self._read()
                if state.state == "HALTED":
                    self._db.rollback()
                    return state
                if now < state.observed_at:
                    state = self._save(state, state="HALTED", code="CLOCK_REGRESSION")
                    self._db.commit()
                    return state
                if state.pending_attempt_id is None:
                    if now < state.next_due_at:
                        self._db.rollback()
                        return state
                    start = self._history_start(state)
                    if not timedelta(0) < now - start <= timedelta(days=1):
                        state = self._save(
                            state, state="HALTED", code="HISTORY_GAP", observed_at=now
                        )
                        self._db.commit()
                        return state
                    # Reserve space for capture outcome and a later capacity halt.
                    if state.revision >= MAX_TRANSITIONS - 2:
                        state = self._save(state, state="HALTED", code="CAPACITY", observed_at=now)
                        self._db.commit()
                        return state
                    state = self._save(
                        state,
                        state="ACTIVE",
                        code=None,
                        observed_at=now,
                        pending_attempt_id=uuid4(),
                        pending_history_start=start,
                        pending_history_end=now,
                    )
                    self._db.commit()
                else:
                    self._db.rollback()
            except Exception:
                if self._db is not None:
                    self._db.rollback()
                return self._unavailable(now)
        return self._capture_pending(state)

    def _capture_pending(self, state):
        request = DemoSyncRequest(
            connection=self.connection,
            history_start=state.pending_history_start,
            history_end=state.pending_history_end,
        )
        attempt = state.pending_attempt_id
        # A crash after projection commit can recover without replaying expired facts.
        projected = self.projection.apply(self.gateway, request, attempt)
        outcome, code = "HEALTHY", None
        if projected.state not in {"APPLIED", "ALREADY_APPLIED"}:
            if projected.code != "SOURCE_WITHHELD":
                outcome, code = "HALTED", "PROJECTION_FAILURE"
            else:
                receipt = self.gateway.capture(request, attempt)
                if receipt.report is not None and receipt.report.state == "CAPTURED":
                    projected = self.projection.apply(self.gateway, request, attempt)
                    if projected.state not in {"APPLIED", "ALREADY_APPLIED"}:
                        outcome, code = "HALTED", "PROJECTION_FAILURE"
                elif (
                    receipt.result is not None
                    and receipt.result.worker_custody == "EXIT_CONFIRMED"
                    and receipt.receipt_ref is not None
                    and receipt.report is not None
                    and receipt.report.state == "WITHHELD"
                    and receipt.report.code in {"CONNECTION", "QUOTE", "DATA_UNAVAILABLE"}
                ):
                    outcome, code = "DEGRADED", "SOURCE_FAILURE"
                else:
                    outcome, code = (
                        "HALTED",
                        "CUSTODY_UNCERTAIN"
                        if receipt.code == "CUSTODY_UNCERTAIN"
                        else "SOURCE_FAILURE",
                    )
        now = utc(self.clock())
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                current = self._read()
                if current != state:
                    self._db.rollback()
                    return current
                if now < state.observed_at:
                    outcome, code, now = "HALTED", "CLOCK_REGRESSION", state.observed_at
                changes = {"state": outcome, "code": code, "observed_at": now}
                if outcome != "HALTED":
                    failures = 0 if outcome == "HEALTHY" else state.consecutive_failures + 1
                    delay = min(
                        self.schedule.max_backoff_seconds,
                        self.schedule.interval_seconds * (2 ** min(failures, 12)),
                    )
                    changes.update(
                        pending_attempt_id=None,
                        pending_history_start=None,
                        pending_history_end=None,
                        consecutive_failures=failures,
                        next_due_at=now + timedelta(seconds=delay),
                    )
                    if outcome == "HEALTHY":
                        changes.update(
                            last_history_end=state.pending_history_end, last_success_at=now
                        )
                result = self._save(state, **changes)
                self._db.commit()
                return result
            except Exception:
                if self._db is not None:
                    self._db.rollback()
                return self._unavailable(now)

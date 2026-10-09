"""Foreground demo observation service and bounded public health publication."""

import argparse
import json
import os
import signal
import sqlite3
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Literal
from uuid import uuid4

from pydantic import UUID4, Field, model_validator

from ai_quant_os.mt5_demo_gateway import DemoObservationGateway, utc
from ai_quant_os.mt5_demo_observation import DemoCaptureRequest, ObservationModel
from ai_quant_os.mt5_sync_controller import (
    DemoSyncController,
    SyncHealth,
    SyncSchedule,
    SyncRecoveryRequest,
    SyncClockRecoveryRequest,
    SyncBackfillRequest,
    SyncWindowReductionRequest,
)
from ai_quant_os.mt5_sync_projection import SyncProjectionStore
from ai_quant_os.mt5_sync_inspection import inspect_sync
from ai_quant_os.mt5_position_comparison import DeclaredOpeningPositions, compare_positions
from ai_quant_os.mt5_money_comparison import DeclaredOpeningMoney, compare_money

MAX_CONFIG_BYTES = 32768
MAX_HEALTH_BYTES = 16384
PASSWORD_ENV = "AI_QUANT_MT5_PASSWORD"
CHECKPOINT_MARKER = "checkpoint-pending.json"


class ServiceRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ServiceConfig(ObservationModel):
    connection: DemoCaptureRequest
    schedule: SyncSchedule
    max_ticks: int = Field(default=10000, ge=1, le=10000, strict=True)
    timer_seconds: int = Field(default=1, ge=1, le=60, strict=True)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration field")
        result[key] = value
    return result


def load_config(path: Path, environment) -> ServiceConfig:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("Configuration bound exceeded")
        values = json.loads(raw, object_pairs_hook=_unique)
        connection = values["connection"]
        if "password" in connection:
            raise ValueError("Password must come from fixed environment")
        password = environment.get(PASSWORD_ENV)
        if connection.get("enabled") is True and not password:
            raise ValueError("Enabled connection requires a password")
        connection["password"] = password or "DISABLED"
        return ServiceConfig.model_validate(values)
    except Exception:
        raise ServiceRejected("CONFIG_INVALID") from None


class ServiceHealth(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_kind: Literal["DEMO_SYNC_SERVICE"] = "DEMO_SYNC_SERVICE"
    entry_point: Literal["DEMO_SYNC_SERVICE"] = "DEMO_SYNC_SERVICE"
    run_id: UUID4
    state: Literal["RUNNING", "STOPPED", "HALTED", "DISABLED"]
    code: Literal["TICK_LIMIT", "SERVICE_FAILURE"] | None = None
    ticks: int = Field(ge=0, le=10000, strict=True)
    published_at: datetime
    valid_until: datetime
    health: SyncHealth | None = None
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def publication_window(self):
        utc(self.published_at)
        utc(self.valid_until)
        if self.valid_until - self.published_at != timedelta(seconds=30):
            raise ValueError("Invalid publication window")
        return self


class ServiceHealthView(ObservationModel):
    state: Literal["AVAILABLE", "STALE", "UNAVAILABLE"]
    record: ServiceHealth | None = None
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False


def publish_health(path: Path, health: ServiceHealth):
    payload = health.model_dump_json().encode()
    if len(payload) > MAX_HEALTH_BYTES:
        raise ValueError("Health publication bound exceeded")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="demo-health-", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def read_health(path: Path, *, clock=lambda: datetime.now(timezone.utc)) -> ServiceHealthView:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_HEALTH_BYTES + 1)
        if len(raw) > MAX_HEALTH_BYTES:
            raise ValueError("Health bound exceeded")
        record = ServiceHealth.model_validate(json.loads(raw, object_pairs_hook=_unique))
        now = utc(clock())
        if now < record.published_at:
            raise ValueError("Future publication")
        return ServiceHealthView(
            state="AVAILABLE" if now < record.valid_until else "STALE", record=record
        )
    except Exception:
        return ServiceHealthView(state="UNAVAILABLE")


def run_service(
    controller: DemoSyncController,
    health_path: Path,
    stop: Event,
    *,
    max_ticks=10000,
    timer_seconds=1,
    clock=lambda: datetime.now(timezone.utc),
    wait=None,
) -> ServiceHealth:
    if (
        type(max_ticks) is not int
        or not 1 <= max_ticks <= 10000
        or type(timer_seconds) is not int
        or not 1 <= timer_seconds <= 60
    ):
        raise ServiceRejected("CONFIG_INVALID")
    wait = wait or stop.wait
    run_id, ticks, health = uuid4(), 0, None

    def publish(state, code=None):
        now = utc(clock())
        record = ServiceHealth(
            run_id=run_id,
            state=state,
            code=code,
            ticks=ticks,
            published_at=now,
            valid_until=now + timedelta(seconds=30),
            health=health,
        )
        try:
            publish_health(health_path, record)
        except Exception:
            raise ServiceRejected("HEALTH_UNAVAILABLE") from None
        return record

    publish("RUNNING")
    while not stop.is_set() and ticks < max_ticks:
        try:
            health = controller.tick()
        except Exception:
            return publish("HALTED", "SERVICE_FAILURE")
        ticks += 1
        if health.state in {"HALTED", "DISABLED"}:
            return publish(health.state)
        publish("RUNNING")
        if ticks < max_ticks and wait(timer_seconds):
            break
    return publish("STOPPED", "TICK_LIMIT" if ticks >= max_ticks else None)


@contextmanager
def service_ownership(path: Path):
    """Hold a local SQLite writer lock until this service exits.

    The file is retained; process exit releases the kernel lock without any
    PID adoption, lease expiry, or deletion of another owner's state.
    """
    connection = None
    try:
        try:
            connection = sqlite3.connect(path, timeout=0, isolation_level=None)
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise ServiceRejected("SERVICE_ALREADY_RUNNING") from None
            raise ServiceRejected("SERVICE_LOCK_UNAVAILABLE") from None
        yield
    finally:
        if connection is not None:
            connection.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run explicitly enrolled demo account observations"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--enable-demo-service", action="store_true")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--recover-pending-attempt")
    actions.add_argument("--resume-clock-halt", action="store_true")
    actions.add_argument("--backfill-history-attempt")
    actions.add_argument("--reduce-history-attempt")
    actions.add_argument("--inspect-state", action="store_true")
    actions.add_argument("--compare-position-history", type=Path)
    actions.add_argument("--compare-money-history", type=Path)
    parser.add_argument("--prior-failed-attempt")
    parser.add_argument("--history-window-seconds", type=int)
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args(argv)
    stop = Event()
    handlers = {}
    try:
        recovery = None
        if args.reduce_history_attempt is None and (
            args.prior_failed_attempt is not None or args.history_window_seconds is not None
        ):
            raise ServiceRejected("RECOVERY_INVALID")
        if (
            args.recover_pending_attempt is not None
            or args.expected_revision is not None
            or args.resume_clock_halt
            or args.backfill_history_attempt is not None
            or args.reduce_history_attempt is not None
        ):
            try:
                if args.reduce_history_attempt is not None:
                    recovery = SyncWindowReductionRequest(
                        expected_revision=args.expected_revision,
                        prior_attempt_id=args.prior_failed_attempt,
                        attempt_id=args.reduce_history_attempt,
                        window_seconds=args.history_window_seconds,
                    )
                elif args.backfill_history_attempt is not None:
                    recovery = SyncBackfillRequest(
                        expected_revision=args.expected_revision,
                        attempt_id=args.backfill_history_attempt,
                    )
                elif args.resume_clock_halt:
                    recovery = SyncClockRecoveryRequest(expected_revision=args.expected_revision)
                else:
                    recovery = SyncRecoveryRequest(
                        pending_attempt_id=args.recover_pending_attempt,
                        expected_revision=args.expected_revision,
                    )
            except Exception:
                raise ServiceRejected("RECOVERY_INVALID") from None
        opening = None
        opening_path = args.compare_position_history or args.compare_money_history
        if opening_path is not None:
            try:
                with opening_path.open("rb") as stream:
                    raw = stream.read(MAX_CONFIG_BYTES + 1)
                if len(raw) > MAX_CONFIG_BYTES:
                    raise ValueError("Opening declaration bound exceeded")
                opening_model = (
                    DeclaredOpeningPositions
                    if args.compare_position_history is not None
                    else DeclaredOpeningMoney
                )
                opening = opening_model.model_validate(json.loads(raw, object_pairs_hook=_unique))
            except Exception:
                raise ServiceRejected("COMPARISON_INVALID") from None
        offline = args.inspect_state or opening is not None
        environment = {PASSWORD_ENV: "INSPECTION_ONLY"} if offline else os.environ
        config = load_config(args.config, environment)
        if config.schedule.enabled and config.connection.enabled and not offline:
            if not args.enable_demo_service:
                raise ServiceRejected("ENABLE_REQUIRED")
            if sys.platform != "win32":
                raise ServiceRejected("WINDOWS_REQUIRED")
        if (recovery is not None or offline) and not all(
            (args.state_dir / name).is_file()
            for name in ("gateway.sqlite", "projection.sqlite", "controller.sqlite")
        ):
            raise ServiceRejected(
                "INSPECTION_STATE_REQUIRED" if offline else "RECOVERY_STATE_REQUIRED"
            )
        args.state_dir.mkdir(parents=True, exist_ok=True)
        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(signum, lambda signum, frame: stop.set())
        with ExitStack() as stack:
            stack.enter_context(service_ownership(args.state_dir / "service-lock.sqlite"))
            if (args.state_dir / CHECKPOINT_MARKER).exists():
                raise ServiceRejected("CHECKPOINT_INCOMPLETE")
            gateway = DemoObservationGateway(args.state_dir / "gateway.sqlite")
            stack.callback(gateway.close)
            projection = SyncProjectionStore(args.state_dir / "projection.sqlite")
            stack.callback(projection.close)
            controller = DemoSyncController(
                args.state_dir / "controller.sqlite",
                config.connection,
                config.schedule,
                gateway,
                projection,
            )
            stack.callback(controller.close)
            if opening is not None:
                result = (
                    compare_money(projection, config.connection, opening)
                    if isinstance(opening, DeclaredOpeningMoney)
                    else compare_positions(projection, config.connection, opening)
                )
                print(result.model_dump_json(), flush=True)
                return 0 if result.state == "CONSISTENT_WITH_DECLARATION" else 2
            if args.inspect_state:
                result = inspect_sync(controller)
                print(result.model_dump_json(), flush=True)
                return 0 if result.state == "READABLE" else 2
            if recovery is not None:
                if isinstance(recovery, SyncWindowReductionRequest):
                    result = controller.reduce_failed_window(recovery)
                elif isinstance(recovery, SyncBackfillRequest):
                    result = controller.backfill_history(recovery)
                elif isinstance(recovery, SyncClockRecoveryRequest):
                    result = controller.recover_clock(recovery)
                else:
                    result = controller.recover_pending(recovery)
                print(result.model_dump_json(), flush=True)
                return 0 if result.state in {"RECOVERED", "RESUMED", "COMPLETED"} else 2
            result = run_service(
                controller,
                args.state_dir / "health.json",
                stop,
                max_ticks=config.max_ticks,
                timer_seconds=config.timer_seconds,
            )
        print(result.model_dump_json(), flush=True)
        return 2 if result.state == "HALTED" else 0
    except ServiceRejected as exc:
        print(
            json.dumps({"state": "HALTED", "code": exc.code, "may_submit_orders": False}),
            flush=True,
        )
        return 2
    except Exception:
        print(
            json.dumps({"state": "HALTED", "code": "SERVICE_FAILURE", "may_submit_orders": False}),
            flush=True,
        )
        return 2
    finally:
        for signum, previous in handlers.items():
            signal.signal(signum, previous)


if __name__ == "__main__":
    raise SystemExit(main())

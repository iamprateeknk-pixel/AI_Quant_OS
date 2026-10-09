"""One-shot demo observation worker and bounded parent supervision.

The parent owns only its Python child. MT5 terminal lifecycle is not attested.
"""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import UUID4

# Direct isolated Python invocation has no project directory on its import path.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_quant_os.mt5_demo_observation import (  # noqa: E402
    DemoCaptureReport,
    DemoCaptureRequest,
    DemoSnapshot,
    ObservationModel,
    capture_demo,
)
from ai_quant_os.mt5_account_sync import (  # noqa: E402
    DemoSyncRequest,
    DemoSyncReport,
    capture_sync,
    account_scope,
    event_ref,
    ORDER_KINDS,
    ORDER_STATES,
    DEAL_KINDS,
)

REQUEST_LIMIT = 16 * 1024
OUTPUT_LIMIT = 64 * 1024
WORKER_SECONDS = 10


class WorkerReply(ObservationModel):
    code: Literal[
        "OK", "INVALID_REQUEST", "TERMINAL_UNAVAILABLE", "SDK_UNAVAILABLE", "WORKER_FAILURE"
    ]
    report: DemoCaptureReport | None = None


class SupervisedCapture(ObservationModel):
    state: Literal["DISABLED", "RETURNED", "WITHHELD"]
    code: (
        Literal[
            "TIMEOUT",
            "OUTPUT_LIMIT",
            "WORKER_EXIT",
            "PROTOCOL",
            "START_FAILURE",
            "BUSY",
            "CUSTODY_UNCERTAIN",
            "INVALID_REQUEST",
        ]
        | None
    ) = None
    report: DemoCaptureReport | None = None
    worker_reply_code: str | None = None
    worker_custody: Literal["NOT_STARTED", "EXIT_CONFIRMED", "UNKNOWN"]
    worker_id: UUID4 | None = None
    terminal_custody: Literal["UNVERIFIED"] = "UNVERIFIED"
    automatic_retry_allowed: Literal[False] = False


class SupervisedSync(SupervisedCapture):
    observation_kind: Literal["ACCOUNT_SYNC"] = "ACCOUNT_SYNC"
    report: DemoSyncReport | None = None


class SyncWorkerReply(WorkerReply):
    observation_kind: Literal["ACCOUNT_SYNC"] = "ACCOUNT_SYNC"
    report: DemoSyncReport | None = None


class SyncWorkerRequest(ObservationModel):
    operation: Literal["CAPTURE_DEMO_ACCOUNT"]
    request: DemoSyncRequest


class WorkerRequest(ObservationModel):
    operation: Literal["CAPTURE_DEMO_QUOTES"]
    request: DemoCaptureRequest


def encode_request(request: DemoCaptureRequest | DemoSyncRequest) -> bytes:
    if isinstance(request, DemoSyncRequest):
        connection = json.loads(encode_request(request.connection))["request"]
        encoded = json.dumps(
            {
                "operation": "CAPTURE_DEMO_ACCOUNT",
                "request": {
                    "connection": connection,
                    "history_start": request.history_start.isoformat(),
                    "history_end": request.history_end.isoformat(),
                },
            }
        ).encode()
        if len(encoded) > REQUEST_LIMIT:
            raise ValueError("Request exceeds worker input limit")
        return encoded
    # The normal public model dump deliberately excludes private connection fields.
    fields = request.model_dump(mode="json")
    fields.update(
        terminal_path=request.terminal_path,
        expected_login=request.expected_login,
        expected_server=request.expected_server.get_secret_value(),
        password=request.password.get_secret_value(),
    )
    encoded = json.dumps({"operation": "CAPTURE_DEMO_QUOTES", "request": fields}).encode()
    if len(encoded) > REQUEST_LIMIT:
        raise ValueError("Request exceeds worker input limit")
    return encoded


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate protocol field")
        result[key] = value
    return result


def validate_capture(
    raw: bytes, request: DemoCaptureRequest | DemoSyncRequest, now: datetime
) -> DemoCaptureReport | DemoSyncReport:
    """Validate public worker facts; this is not proof of native attestation."""
    if isinstance(request, DemoSyncRequest):
        return validate_sync(raw, request, now)
    report = DemoCaptureReport.model_validate(json.loads(raw, object_pairs_hook=unique_object))
    if report.state == "DISABLED":
        if request.enabled or report.code != "DISABLED" or report.snapshot is not None:
            raise ValueError("Invalid disabled report")
    elif report.state == "WITHHELD":
        if report.code is None or report.code == "DISABLED" or report.snapshot is not None:
            raise ValueError("Invalid withheld report")
    else:
        snapshot = report.snapshot
        if not request.enabled or report.code is not None or snapshot is None:
            raise ValueError("Invalid captured report")
        times = [now, snapshot.capture_started_at, snapshot.observed_at, snapshot.valid_until]
        if any(t.tzinfo is None or t.utcoffset() != timedelta(0) for t in times):
            raise ValueError("UTC timestamps required")
        if (
            snapshot.enrollment_id != request.enrollment_id
            or snapshot.sdk_version != request.sdk_version
            or not snapshot.capture_started_at <= snapshot.observed_at <= now < snapshot.valid_until
            or snapshot.observed_at - snapshot.capture_started_at > timedelta(seconds=5)
            or [(q.instrument_id, q.venue_symbol) for q in snapshot.quotes]
            != [(s.instrument_id, s.venue_symbol) for s in request.symbols]
            or any(q.observed_at > snapshot.observed_at for q in snapshot.quotes)
            or snapshot.valid_until
            != min(q.observed_at + timedelta(seconds=3) for q in snapshot.quotes)
        ):
            raise ValueError("Observation binding or freshness invalid")
    return report


def validate_sync(raw: bytes, request: DemoSyncRequest, now: datetime) -> DemoSyncReport:
    report = DemoSyncReport.model_validate(json.loads(raw, object_pairs_hook=unique_object))
    if report.state == "DISABLED":
        if request.enabled or report.code != "DISABLED" or report.batch is not None:
            raise ValueError("Invalid disabled sync")
    elif report.state == "WITHHELD":
        if report.code in (None, "DISABLED") or report.batch is not None:
            raise ValueError("Invalid withheld sync")
    else:
        batch = report.batch
        if not request.enabled or report.code is not None or batch is None:
            raise ValueError("Invalid captured sync")
        snapshot = DemoSnapshot(
            enrollment_id=batch.enrollment_id,
            sdk_version=batch.sdk_version,
            capture_started_at=batch.capture_started_at,
            observed_at=batch.observed_at,
            valid_until=batch.valid_until,
            quotes=batch.quotes,
        )
        quote_report = DemoCaptureReport(
            state="CAPTURED", code=None, snapshot=snapshot, trace_id=report.trace_id
        )
        validate_capture(quote_report.model_dump_json().encode(), request.connection, now)
        if (
            batch.account_scope_ref != account_scope(request.connection)
            or batch.account.currency != request.connection.expected_currency
            or batch.history_start != request.history_start
            or batch.history_end != request.history_end
            or batch.history_end > batch.capture_started_at
        ):
            raise ValueError("Sync scope or history binding invalid")
        quotes = {q.venue_symbol: q for q in batch.quotes}

        def quote_for(item):
            quote = quotes.get(item.venue_symbol)
            if quote is None or quote.instrument_id != item.instrument_id:
                raise ValueError("Unmapped sync record")
            return quote

        def aware(t):
            if t.tzinfo is None or t.utcoffset() != timedelta(0):
                raise ValueError("UTC sync timestamps required")

        if len({p.ticket for p in batch.positions}) != len(batch.positions) or len(
            {p.identifier for p in batch.positions}
        ) != len(batch.positions):
            raise ValueError("Duplicate position")
        for p in batch.positions:
            quote = quote_for(p)
            aware(p.created_at)
            aware(p.updated_at)
            if (
                not p.created_at <= p.updated_at <= batch.observed_at
                or p.currency != batch.account.currency
                or p.quantity_units != p.volume_lots * quote.contract_size
            ):
                raise ValueError("Invalid position facts")
        if len({o.ticket for o in batch.orders}) != len(batch.orders):
            raise ValueError("Duplicate order")
        for o in batch.orders:
            quote_for(o)
            aware(o.created_at)
            if (
                o.kind not in ORDER_KINDS
                or o.state not in ORDER_STATES.values()
                or o.remaining_lots > o.initial_lots
                or o.created_at > batch.observed_at
                or (ORDER_KINDS.index(o.kind) >= 2 and o.price_open <= 0)
                or (ORDER_KINDS.index(o.kind) >= 6 and o.stop_limit_price <= 0)
            ):
                raise ValueError("Invalid active order facts")
        deals = (*batch.fills, *batch.cash_events)
        if len(deals) > 16 or len({d.ticket for d in deals}) != len(deals):
            raise ValueError("Duplicate or excessive history records")
        for d in deals:
            aware(d.observed_at)
            if (
                d.event_ref != event_ref(batch.account_scope_ref, d.ticket)
                or d.currency != batch.account.currency
                or not batch.history_start <= d.observed_at <= batch.history_end
            ):
                raise ValueError("Invalid deal identity or window")
        for d in batch.fills:
            quote = quote_for(d)
            if (
                d.order_ticket <= 0
                or d.position_identifier <= 0
                or d.quantity_units != d.volume_lots * quote.contract_size
            ):
                raise ValueError("Invalid execution observation")
        if any(d.kind not in DEAL_KINDS[2:] for d in batch.cash_events):
            raise ValueError("Trading deal mislabeled as cash")
        for collection in (batch.fills, batch.cash_events):
            if list(collection) != sorted(collection, key=lambda d: (d.observed_at, d.ticket)):
                raise ValueError("History ordering invalid")
    return report


class BoundedPipes:
    def __init__(self, process: subprocess.Popen, payload: bytes):
        self.output = bytearray()
        self.total = 0
        self.overflow = threading.Event()
        self.failed = threading.Event()
        self.lock = threading.Lock()
        self.streams = (process.stdout, process.stderr, process.stdin)
        self.threads = [
            threading.Thread(target=self.read, args=(process.stdout, True), daemon=True),
            threading.Thread(target=self.read, args=(process.stderr, False), daemon=True),
            threading.Thread(target=self.write, args=(process.stdin, payload), daemon=True),
        ]

    def start(self):
        for thread in self.threads:
            thread.start()

    def read(self, stream, retain):
        try:
            while chunk := stream.read(4096):
                with self.lock:
                    self.total += len(chunk)
                    if self.total > OUTPUT_LIMIT:
                        self.overflow.set()
                        return
                    if retain:
                        self.output.extend(chunk)
        except (OSError, ValueError):
            self.failed.set()
        finally:
            stream.close()

    def write(self, stream, payload):
        try:
            remaining = memoryview(payload)
            while remaining:
                written = stream.write(remaining)
                if not written:
                    raise OSError("Worker pipe closed")
                remaining = remaining[written:]
        except (OSError, ValueError):
            self.failed.set()
        finally:
            stream.close()


_supervision_lock = threading.Lock()
_uncertain_child: tuple[UUID, subprocess.Popen, BoundedPipes] | None = None
_last_confirmed_exit: UUID | None = None


def _inspect_locked() -> SupervisedCapture:
    global _uncertain_child, _last_confirmed_exit
    if _uncertain_child is None:
        return SupervisedCapture(state="WITHHELD", worker_custody="NOT_STARTED")
    worker_id, process, pipes = _uncertain_child
    confirmed = process.poll() is not None and all(not t.is_alive() for t in pipes.threads)
    if confirmed:
        _last_confirmed_exit = worker_id
        _uncertain_child = None
    return SupervisedCapture(
        state="WITHHELD",
        worker_id=worker_id,
        worker_custody="EXIT_CONFIRMED" if confirmed else "UNKNOWN",
        code=None if confirmed else "CUSTODY_UNCERTAIN",
    )


def inspect_uncertain_worker() -> SupervisedCapture:
    """Poll the retained exact child handle; do not infer terminal ownership."""
    if not _supervision_lock.acquire(blocking=False):
        return SupervisedCapture(state="WITHHELD", code="BUSY", worker_custody="NOT_STARTED")
    try:
        return _inspect_locked()
    finally:
        _supervision_lock.release()


def confirmed_worker_exit(worker_id: UUID) -> SupervisedCapture:
    """Read the most recent verified exit by exact UUID, including interrupt cleanup.

    This bounded in-process proof contains no native observation and does not
    survive owner-process restart. It is never evidence for another worker UUID.
    """
    if not isinstance(worker_id, UUID) or worker_id.version != 4:
        raise ValueError("Worker identity must be UUID4")
    if not _supervision_lock.acquire(blocking=False):
        return SupervisedCapture(state="WITHHELD", code="BUSY", worker_custody="NOT_STARTED")
    try:
        confirmed = _last_confirmed_exit == worker_id
        return SupervisedCapture(
            state="WITHHELD",
            worker_id=worker_id,
            worker_custody="EXIT_CONFIRMED" if confirmed else "UNKNOWN",
            code=None if confirmed else "CUSTODY_UNCERTAIN",
        )
    finally:
        _supervision_lock.release()


def supervise_demo(
    request: DemoCaptureRequest | DemoSyncRequest,
    *,
    clock=lambda: datetime.now(timezone.utc),
    worker_id: UUID | None = None,
) -> SupervisedCapture:
    result_model = SupervisedSync if isinstance(request, DemoSyncRequest) else SupervisedCapture
    report_model = DemoSyncReport if isinstance(request, DemoSyncRequest) else DemoCaptureReport
    if worker_id is not None and (not isinstance(worker_id, UUID) or worker_id.version != 4):
        raise ValueError("Worker identity must be UUID4")
    if not request.enabled:
        return result_model(
            state="DISABLED",
            report=report_model(state="DISABLED", code="DISABLED"),
            worker_custody="NOT_STARTED",
        )
    if not _supervision_lock.acquire(blocking=False):
        return result_model(state="WITHHELD", code="BUSY", worker_custody="NOT_STARTED")
    try:
        retained = _inspect_locked()
        if retained.worker_custody == "UNKNOWN":
            return result_model.model_validate(retained.model_dump())
        return _run_supervised(request, clock=clock, worker_id=worker_id or uuid4())
    finally:
        _supervision_lock.release()


def _run_supervised(
    request: DemoCaptureRequest | DemoSyncRequest, *, clock, worker_id: UUID
) -> SupervisedCapture | SupervisedSync:
    global _uncertain_child
    result_model = SupervisedSync if isinstance(request, DemoSyncRequest) else SupervisedCapture
    try:
        payload = encode_request(request)
    except ValueError:
        return result_model(state="WITHHELD", code="INVALID_REQUEST", worker_custody="NOT_STARTED")
    deadline = time.monotonic() + WORKER_SECONDS
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env={
                key: value
                for key, value in os.environ.items()
                if key.upper() != "AI_QUANT_MT5_PASSWORD"
            },
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError:
        return result_model(state="WITHHELD", code="START_FAILURE", worker_custody="NOT_STARTED")
    pipes = BoundedPipes(process, payload)
    _uncertain_child = (worker_id, process, pipes)
    try:
        pipes.start()
        return _await_reply(process, pipes, worker_id, request, deadline, clock)
    except BaseException as exc:
        stop_owned_worker(process, pipes)
        custody = _inspect_locked().worker_custody
        if not isinstance(exc, Exception):
            raise
        return result_model(
            state="WITHHELD", code="WORKER_EXIT", worker_id=worker_id, worker_custody=custody
        )
    finally:
        _inspect_locked()


def stop_owned_worker(process, pipes):
    try:
        if process.poll() is None:
            process.kill()  # Exact child handle, never a terminal or process tree.
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for thread, stream in zip(pipes.threads, pipes.streams):
        if thread.ident is not None:
            thread.join(timeout=0.05)
        else:
            stream.close()


def _await_reply(process, pipes, worker_id, request, deadline, clock):
    result_model = SupervisedSync if isinstance(request, DemoSyncRequest) else SupervisedCapture
    reply_model = SyncWorkerReply if isinstance(request, DemoSyncRequest) else WorkerReply
    code = None
    while True:
        if pipes.overflow.is_set():
            code = "OUTPUT_LIMIT"
            break
        if time.monotonic() >= deadline:
            code = "TIMEOUT"
            break
        if process.poll() is not None and all(not t.is_alive() for t in pipes.threads):
            break
        time.sleep(0.01)
    if code is not None:
        stop_owned_worker(process, pipes)
    custody = (
        "EXIT_CONFIRMED"
        if process.poll() is not None and all(not t.is_alive() for t in pipes.threads)
        else "UNKNOWN"
    )
    if code is not None:
        return result_model(
            state="WITHHELD", code=code, worker_custody=custody, worker_id=worker_id
        )
    if process.returncode != 0 or pipes.failed.is_set():
        return result_model(
            state="WITHHELD", code="WORKER_EXIT", worker_custody=custody, worker_id=worker_id
        )
    try:
        reply = reply_model.model_validate(
            json.loads(pipes.output, object_pairs_hook=unique_object)
        )
        if reply.code != "OK":
            if reply.report is not None:
                raise ValueError("Failure cannot carry observations")
            return result_model(
                state="WITHHELD",
                worker_reply_code=reply.code,
                worker_custody=custody,
                worker_id=worker_id,
            )
        if reply.report is None:
            raise ValueError("Missing report")
        report = validate_capture(reply.report.model_dump_json().encode(), request, clock())
        return result_model(
            state="RETURNED",
            report=report,
            worker_reply_code="OK",
            worker_custody=custody,
            worker_id=worker_id,
        )
    except Exception:
        return result_model(
            state="WITHHELD", code="PROTOCOL", worker_custody=custody, worker_id=worker_id
        )


def worker_request(raw: bytes) -> WorkerReply:
    try:
        if len(raw) > REQUEST_LIMIT:
            raise ValueError("Input limit")
        data = json.loads(raw, object_pairs_hook=unique_object)
        request_model = (
            SyncWorkerRequest
            if isinstance(data, dict) and data.get("operation") == "CAPTURE_DEMO_ACCOUNT"
            else WorkerRequest
        )
        request = request_model.model_validate(data).request
    except Exception:
        return WorkerReply(code="INVALID_REQUEST")
    reply_model = SyncWorkerReply if isinstance(request, DemoSyncRequest) else WorkerReply
    report_model = DemoSyncReport if isinstance(request, DemoSyncRequest) else DemoCaptureReport
    connection = request.connection if isinstance(request, DemoSyncRequest) else request
    if not request.enabled:
        return reply_model(code="OK", report=report_model(state="DISABLED", code="DISABLED"))
    if os.name != "nt" or not Path(connection.terminal_path).is_file():
        return reply_model(code="TERMINAL_UNAVAILABLE")
    try:
        import MetaTrader5 as native
    except Exception:
        return reply_model(code="SDK_UNAVAILABLE")
    capture = capture_sync if isinstance(request, DemoSyncRequest) else capture_demo
    return reply_model(
        code="OK", report=capture(request, native, clock=lambda: datetime.now(timezone.utc))
    )


def main() -> int:
    # Native diagnostics (including C-level writes) cannot enter the report pipe.
    report_fd = os.dup(sys.stdout.fileno())
    with open(os.devnull, "wb") as discard:
        os.dup2(discard.fileno(), sys.stdout.fileno())
        os.dup2(discard.fileno(), sys.stderr.fileno())
    try:
        raw = sys.stdin.buffer.read(REQUEST_LIMIT + 1)
        reply = worker_request(raw)
    except Exception:
        reply = WorkerReply(code="WORKER_FAILURE")
    with os.fdopen(report_fd, "wb") as output:
        output.write(reply.model_dump_json().encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

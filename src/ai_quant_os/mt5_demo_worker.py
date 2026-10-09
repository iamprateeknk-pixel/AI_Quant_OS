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
    ObservationModel,
    capture_demo,
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


class WorkerRequest(ObservationModel):
    operation: Literal["CAPTURE_DEMO_QUOTES"]
    request: DemoCaptureRequest


def encode_request(request: DemoCaptureRequest) -> bytes:
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


def validate_capture(raw: bytes, request: DemoCaptureRequest, now: datetime) -> DemoCaptureReport:
    """Validate public worker facts; this is not proof of native attestation."""
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


def _inspect_locked() -> SupervisedCapture:
    global _uncertain_child
    if _uncertain_child is None:
        return SupervisedCapture(state="WITHHELD", worker_custody="NOT_STARTED")
    worker_id, process, pipes = _uncertain_child
    confirmed = process.poll() is not None and all(not t.is_alive() for t in pipes.threads)
    if confirmed:
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


def supervise_demo(
    request: DemoCaptureRequest, *, clock=lambda: datetime.now(timezone.utc)
) -> SupervisedCapture:
    if not request.enabled:
        return SupervisedCapture(
            state="DISABLED",
            report=DemoCaptureReport(state="DISABLED", code="DISABLED"),
            worker_custody="NOT_STARTED",
        )
    if not _supervision_lock.acquire(blocking=False):
        return SupervisedCapture(state="WITHHELD", code="BUSY", worker_custody="NOT_STARTED")
    try:
        retained = _inspect_locked()
        if retained.worker_custody == "UNKNOWN":
            return retained
        return _run_supervised(request, clock=clock)
    finally:
        _supervision_lock.release()


def _run_supervised(request: DemoCaptureRequest, *, clock) -> SupervisedCapture:
    global _uncertain_child
    try:
        payload = encode_request(request)
    except ValueError:
        return SupervisedCapture(
            state="WITHHELD", code="INVALID_REQUEST", worker_custody="NOT_STARTED"
        )
    deadline = time.monotonic() + WORKER_SECONDS
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError:
        return SupervisedCapture(
            state="WITHHELD", code="START_FAILURE", worker_custody="NOT_STARTED"
        )
    worker_id = uuid4()
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
        return SupervisedCapture(
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
        return SupervisedCapture(
            state="WITHHELD", code=code, worker_custody=custody, worker_id=worker_id
        )
    if process.returncode != 0 or pipes.failed.is_set():
        return SupervisedCapture(
            state="WITHHELD", code="WORKER_EXIT", worker_custody=custody, worker_id=worker_id
        )
    try:
        reply = WorkerReply.model_validate(
            json.loads(pipes.output, object_pairs_hook=unique_object)
        )
        if reply.code != "OK":
            if reply.report is not None:
                raise ValueError("Failure cannot carry observations")
            return SupervisedCapture(
                state="WITHHELD",
                worker_reply_code=reply.code,
                worker_custody=custody,
                worker_id=worker_id,
            )
        if reply.report is None:
            raise ValueError("Missing report")
        report = validate_capture(reply.report.model_dump_json().encode(), request, clock())
        return SupervisedCapture(
            state="RETURNED",
            report=report,
            worker_reply_code="OK",
            worker_custody=custody,
            worker_id=worker_id,
        )
    except Exception:
        return SupervisedCapture(
            state="WITHHELD", code="PROTOCOL", worker_custody=custody, worker_id=worker_id
        )


def worker_request(raw: bytes) -> WorkerReply:
    try:
        if len(raw) > REQUEST_LIMIT:
            raise ValueError("Input limit")
        request = WorkerRequest.model_validate(
            json.loads(raw, object_pairs_hook=unique_object)
        ).request
    except Exception:
        return WorkerReply(code="INVALID_REQUEST")
    if not request.enabled:
        return WorkerReply(code="OK", report=DemoCaptureReport(state="DISABLED", code="DISABLED"))
    if os.name != "nt" or not Path(request.terminal_path).is_file():
        return WorkerReply(code="TERMINAL_UNAVAILABLE")
    try:
        import MetaTrader5 as native
    except Exception:
        return WorkerReply(code="SDK_UNAVAILABLE")
    return WorkerReply(
        code="OK", report=capture_demo(request, native, clock=lambda: datetime.now(timezone.utc))
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

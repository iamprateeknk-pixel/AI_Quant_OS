"""Strict, bounded message contract for the isolated HOLDOUT validator."""

from __future__ import annotations

import json
import os
import re
import time
import base64
from hashlib import sha256
from decimal import Decimal
from typing import Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_FRAME_BYTES = 2097152
_RESPONSE_RECEIVED_ACK = b"ACK1"
HOLDOUT_PIPE_NAME = r"\\.\pipe\AIQuantHoldout.v3"
_MAX_ID_LENGTH = 512
_SERVICE_SID_PATTERN = re.compile(r"^S-1-5-80-(?:\d+-){4}\d+$")
FrameModel = TypeVar("FrameModel", bound=BaseModel)


class HoldoutValidationPreparation(BaseModel):
    """Checked, non-HOLDOUT inputs the operator API sends for service-side packaging."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    graph_json: str = Field(min_length=2, max_length=262144)
    candidate_json: str = Field(min_length=2, max_length=65536)
    training_receipt_json: str = Field(min_length=2, max_length=4096)
    training_raw_base64: str = Field(min_length=4, max_length=1398104)
    plan_json: str = Field(min_length=2, max_length=8192)
    captured_at: AwareDatetime

    @field_validator("training_raw_base64")
    @classmethod
    def validate_training_bytes(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Invalid validation training snapshot") from error
        if (
            not 0 < len(raw) <= 1048576
            or base64.b64encode(raw).decode("ascii") != value
        ):
            raise ValueError("Invalid validation training snapshot")
        return value


def _identifier(value: str) -> str:
    if (
        not value
        or len(value) > _MAX_ID_LENGTH
        or any(ord(char) < 32 for char in value)
        or "\\" in value
        or "/" in value
    ):
        raise ValueError("Invalid RPC identifier")
    return value


class HoldoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: str = Field(min_length=1, max_length=128)
    operation: Literal[
        "VALIDATION_RUN",
        "STRESS_RUN",
        "VALIDATION_CONTEXT",
        "STRESS_CONTEXT",
        "VALIDATION_PREPARE",
        "VALIDATION_PACKAGE_METADATA",
    ]
    agent_id: Literal["A10", "A11"]
    agent_version: Literal["0.2.0"]
    task_id: str = Field(min_length=1, max_length=128)
    context_ref: str = Field(min_length=1, max_length=128)
    candidate_ref: str | None = Field(default=None, min_length=1, max_length=512)
    training_ref: str | None = Field(default=None, min_length=1, max_length=512)
    holdout_ref: str | None = Field(default=None, min_length=1, max_length=512)
    evidence_ref: str = Field(min_length=1, max_length=512)
    preparation: HoldoutValidationPreparation | None = None

    @field_validator(
        "request_id",
        "task_id",
        "context_ref",
        "evidence_ref",
    )
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("candidate_ref", "training_ref", "holdout_ref")
    @classmethod
    def validate_optional_identifier(cls, value: str | None) -> str | None:
        return None if value is None else _identifier(value)

    def model_post_init(self, __context) -> None:
        if (self.agent_id, self.operation) not in {
            ("A10", "VALIDATION_RUN"),
            ("A11", "STRESS_RUN"),
            ("A10", "VALIDATION_CONTEXT"),
            ("A11", "STRESS_CONTEXT"),
            ("A10", "VALIDATION_PREPARE"),
            ("A10", "VALIDATION_PACKAGE_METADATA"),
        }:
            raise ValueError("RPC operation is not authorized for this agent")
        refs = (self.candidate_ref, self.training_ref, self.holdout_ref)
        if self.operation.endswith("_CONTEXT") or self.operation == "VALIDATION_PACKAGE_METADATA":
            if any(value is not None for value in refs):
                raise ValueError("Context request cannot supply archive scope refs")
        elif any(value is None for value in refs):
            raise ValueError("Validation requests require complete archive scope refs")
        if (self.operation == "VALIDATION_PREPARE") != (self.preparation is not None):
            raise ValueError("Validation preparation payload does not match operation")


class HoldoutScenarioAggregate(BaseModel):
    """Per-scenario totals only; never carries bar or order-level observations."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str = Field(pattern=r"^[A-Za-z0-9_:-]{1,64}$")
    dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    net_pnl: Decimal = Field(allow_inf_nan=False)
    pnl_currency: Literal["USD", "JPY"]
    native_order_count: int = Field(strict=True, ge=0, le=4096)


class HoldoutAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    outcome: Literal["PREREQUISITE_FAILED", "REJECTED", "VALIDATION_INCOMPLETE"] | None = None
    state: Literal["PASS", "FAIL", "PENDING"] | None = None
    gate_count: int | None = Field(default=None, ge=0, le=32)
    scenario_count: int | None = Field(default=None, ge=0, le=16)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=32)
    scenarios: tuple[HoldoutScenarioAggregate, ...] = Field(default=(), max_length=16)

    @field_validator("reason_codes")
    @classmethod
    def validate_reason_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not value or len(value) > 96 or not value.replace("_", "").isalnum() for value in values
        ):
            raise ValueError("Invalid aggregate reason code")
        return values

    @model_validator(mode="after")
    def validate_operation_shape(self):
        if self.state is None:
            if self.scenario_count is not None or self.scenarios:
                raise ValueError("Validation aggregate cannot contain stress scenarios")
        elif (
            self.outcome is not None
            or self.gate_count is not None
            or not self.scenarios
            or self.scenario_count != len(self.scenarios)
            or len(self.reason_codes) != 1
        ):
            raise ValueError("Stress aggregate scenario binding mismatch")
        return self


class HoldoutContextSnapshot(BaseModel):
    """Bounded JSON snapshot of typed, summary-only context evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agent_id: Literal["A10", "A11"]
    payload_json: str = Field(min_length=2, max_length=49152)


class HoldoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    context_ref: str = Field(min_length=1, max_length=128)
    status: Literal[
        "CHECKED_ARCHIVED_OBSERVATION",
        "CHECKED_ARCHIVED_STRESS",
        "CHECKED_ARCHIVED_VALIDATION_CONTEXT",
        "CHECKED_ARCHIVED_STRESS_CONTEXT",
        "CHECKED_VALIDATION_PACKAGE_PREPARED",
        "CHECKED_VALIDATION_PACKAGE_METADATA",
    ]
    evidence_ref: str = Field(min_length=1, max_length=512)
    summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    aggregate: HoldoutAggregate
    context_snapshot: HoldoutContextSnapshot | None = None
    package_metadata_json: str | None = Field(default=None, max_length=16384)

    @field_validator("request_id", "task_id", "context_ref", "evidence_ref")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _identifier(value)


class HoldoutServiceFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    context_ref: str = Field(min_length=1, max_length=128)
    error_code: Literal["REQUEST_REFUSED", "SERVICE_TIMEOUT", "SERVICE_UNAVAILABLE"]

    @field_validator("request_id", "task_id", "context_ref")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _identifier(value)


class HoldoutRpcError(ConnectionError):
    """Sanitized service failure carrying only a fixed protocol error code."""

    def __init__(self, error_code: str) -> None:
        if error_code not in {"REQUEST_REFUSED", "SERVICE_TIMEOUT", "SERVICE_UNAVAILABLE"}:
            raise ValueError("Invalid HOLDOUT RPC failure code")
        self.error_code = error_code
        super().__init__(f"HOLDOUT validator returned {error_code}")


def encode_frame(value: BaseModel | dict) -> bytes:
    """Encode a strict JSON message with a four-byte network-order length."""
    if isinstance(value, BaseModel):
        body = value.model_dump_json().encode("utf-8")
    elif isinstance(value, dict):
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    else:
        raise TypeError("RPC frame must be a model or object")
    if not body or len(body) > MAX_FRAME_BYTES:
        raise ValueError("RPC frame exceeds byte bound")
    return len(body).to_bytes(4, "big") + body


def decode_frame(frame: bytes, model: type[FrameModel]) -> FrameModel:
    """Decode exactly one bounded frame; never include message contents in errors."""
    if type(frame) is not bytes or len(frame) < 5:
        raise ValueError("Invalid RPC frame length")
    declared = int.from_bytes(frame[:4], "big")
    body = frame[4:]
    if not 1 <= declared <= MAX_FRAME_BYTES or declared != len(body):
        raise ValueError("Invalid RPC frame length")
    try:
        return model.model_validate_json(body, strict=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid RPC JSON message") from None


def stress_summary_sha256(state: str, reason: str, scenarios) -> str:
    """Digest the bounded A11 aggregate fields in a shared canonical form."""
    if state not in {"PASS", "FAIL", "PENDING"} or not reason or len(reason) > 128:
        raise ValueError("Invalid stress summary")
    rows = []
    for row in scenarios:
        encoded = (
            row.model_dump_json()
            if isinstance(row, BaseModel)
            else json.dumps(row, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        )
        rows.append(HoldoutScenarioAggregate.model_validate_json(encoded).model_dump(mode="json"))
    if not 1 <= len(rows) <= 16:
        raise ValueError("Invalid stress scenario count")
    canonical = json.dumps(
        {"state": state, "reason": reason, "scenarios": rows},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def process_rpc_frame(frame: bytes, handler) -> bytes | None:
    """Process one request; malformed frames receive no content-bearing reply."""
    try:
        request = decode_frame(frame, HoldoutRequest)
    except ValueError:
        return None
    try:
        response = handler(request)
        if type(response) is not HoldoutResponse:
            raise ValueError("Invalid validator result")
        verify_response(request, response)
        return encode_frame(response)
    except PermissionError:
        code = "REQUEST_REFUSED"
    except TimeoutError:
        code = "SERVICE_TIMEOUT"
    except Exception:
        code = "SERVICE_UNAVAILABLE"
    return encode_frame(
        HoldoutServiceFailure(
            request_id=request.request_id,
            task_id=request.task_id,
            context_ref=request.context_ref,
            error_code=code,
        )
    )


def verify_response(request: HoldoutRequest, response: HoldoutResponse) -> HoldoutResponse:
    """Bind the service reply to the exact request identity and operation."""
    if (response.request_id, response.task_id, response.context_ref) != (
        request.request_id,
        request.task_id,
        request.context_ref,
    ):
        raise ValueError("Validator response identity mismatch")
    expected_status = {
        "VALIDATION_RUN": "CHECKED_ARCHIVED_OBSERVATION",
        "STRESS_RUN": "CHECKED_ARCHIVED_STRESS",
        "VALIDATION_CONTEXT": "CHECKED_ARCHIVED_VALIDATION_CONTEXT",
        "STRESS_CONTEXT": "CHECKED_ARCHIVED_STRESS_CONTEXT",
        "VALIDATION_PREPARE": "CHECKED_VALIDATION_PACKAGE_PREPARED",
        "VALIDATION_PACKAGE_METADATA": "CHECKED_VALIDATION_PACKAGE_METADATA",
    }[request.operation]
    if response.status != expected_status:
        raise ValueError("Validator response operation mismatch")
    if response.evidence_ref != request.evidence_ref:
        raise ValueError("Validator response evidence identity mismatch")
    aggregate = response.aggregate
    if request.operation in {"VALIDATION_RUN", "VALIDATION_CONTEXT"}:
        if aggregate.outcome is None or aggregate.gate_count is None or aggregate.state is not None:
            raise ValueError("Validator response aggregate operation mismatch")
    elif request.operation == "STRESS_RUN" or request.operation == "STRESS_CONTEXT":
        if aggregate.state is None:
            raise ValueError("Validator response aggregate operation mismatch")
    elif (
        aggregate.state is not None
        or aggregate.outcome is not None
        or aggregate.gate_count is not None
        or aggregate.scenarios
    ):
        raise ValueError("Validator response aggregate operation mismatch")
    needs_snapshot = request.operation in {"VALIDATION_CONTEXT", "STRESS_CONTEXT"}
    if needs_snapshot != (response.context_snapshot is not None):
        raise ValueError("Validator context snapshot operation mismatch")
    if (
        request.operation in {"VALIDATION_PREPARE", "VALIDATION_PACKAGE_METADATA"}
    ) != (response.package_metadata_json is not None):
        raise ValueError("Validator package metadata operation mismatch")
    if (
        response.context_snapshot is not None
        and response.context_snapshot.agent_id != request.agent_id
    ):
        raise ValueError("Validator context snapshot agent mismatch")
    return response


class HoldoutRpcClient:
    """One-shot bounded client; an uncertain exchange is never automatically retried."""

    def __init__(self, transport, *, timeout_ms: int = 5000) -> None:
        if type(timeout_ms) is not int or not 50 <= timeout_ms <= 30000:
            raise ValueError("Invalid validator RPC deadline")
        self._transport = transport
        self._timeout_ms = timeout_ms

    def call(self, request: HoldoutRequest) -> HoldoutResponse:
        if type(request) is not HoldoutRequest:
            raise TypeError("Strict validator RPC request required")
        try:
            frame = self._transport.exchange(encode_frame(request), timeout_ms=self._timeout_ms)
        except TimeoutError:
            raise TimeoutError("HOLDOUT validator request timed out") from None
        except (PermissionError, ValueError):
            raise
        except Exception:
            raise ConnectionError("HOLDOUT validator exchange failed") from None
        if type(frame) is not bytes or len(frame) < 5:
            raise ValueError("Invalid RPC frame length")
        declared = int.from_bytes(frame[:4], "big")
        if not 1 <= declared <= MAX_FRAME_BYTES or declared != len(frame) - 4:
            raise ValueError("Invalid RPC frame length")
        try:
            value = json.loads(frame[4:])
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Invalid RPC JSON message") from None
        if isinstance(value, dict) and "error_code" in value:
            try:
                failure = HoldoutServiceFailure.model_validate_json(frame[4:], strict=True)
            except (ValueError, TypeError):
                raise ValueError("Invalid validator error response") from None
            if (failure.request_id, failure.task_id, failure.context_ref) != (
                request.request_id,
                request.task_id,
                request.context_ref,
            ):
                raise ValueError("Validator failure identity mismatch")
            raise HoldoutRpcError(failure.error_code)
        try:
            response = HoldoutResponse.model_validate_json(frame[4:], strict=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid validator response") from None
        return verify_response(request, response)


class UnavailableHoldoutRpcClient:
    """Fail-closed client used when the isolated Windows service cannot be addressed."""

    def call(self, request: HoldoutRequest) -> HoldoutResponse:
        if type(request) is not HoldoutRequest:
            raise TypeError("Strict validator RPC request required")
        raise ConnectionError("HOLDOUT validator service is unavailable")


def installed_windows_holdout_client():
    """Build the production client from the installed service identity, when available."""
    if os.name != "nt":
        return UnavailableHoldoutRpcClient()
    try:
        from ai_quant_os.holdout_service_security import service_sid_for_name

        import win32security

        service_sid = service_sid_for_name("AIQuantHoldout", security=win32security)
    except Exception:
        # An uninstalled service must not prevent the PAPER API from starting.
        return UnavailableHoldoutRpcClient()
    return HoldoutRpcClient(WindowsNamedPipeTransport(service_sid))


class WindowsNamedPipeTransport:
    """Bounded one-request Windows pipe exchange with server-token verification."""

    def __init__(self, service_sid: str, *, pipe_name: str = HOLDOUT_PIPE_NAME) -> None:
        if not _SERVICE_SID_PATTERN.fullmatch(service_sid):
            raise ValueError("Invalid validator service SID")
        if not pipe_name.startswith(r"\\.\pipe\AIQuantHoldout.") or ".." in pipe_name:
            raise ValueError("Invalid validator RPC pipe name")
        self._service_sid = service_sid
        self._pipe_name = pipe_name

    def exchange(self, frame: bytes, *, timeout_ms: int) -> bytes:
        if os.name != "nt":
            raise OSError("Validator RPC requires Windows")
        if type(timeout_ms) is not int or not 50 <= timeout_ms <= 30000:
            raise ValueError("Invalid validator RPC deadline")
        if type(frame) is not bytes or not 5 <= len(frame) <= MAX_FRAME_BYTES + 4:
            raise ValueError("Invalid validator RPC frame")
        import pywintypes
        import win32api
        import win32con
        import win32event
        import win32file
        import win32pipe
        import win32security
        import winerror

        deadline = time.monotonic() + timeout_ms / 1000
        handle = None
        try:
            remaining = self._remaining_ms(deadline)
            win32pipe.WaitNamedPipe(self._pipe_name, remaining)
            handle = win32file.CreateFile(
                self._pipe_name,
                win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                0,
                None,
                win32con.OPEN_EXISTING,
                win32con.FILE_FLAG_OVERLAPPED,
                None,
            )
            from ai_quant_os.holdout_service_security import verify_pipe_server_identity

            verify_pipe_server_identity(
                handle,
                self._service_sid,
                pipe_api=win32pipe,
                process_api=win32api,
                security=win32security,
                process_query_right=win32con.PROCESS_QUERY_LIMITED_INFORMATION,
            )
            self._write_all(handle, frame, deadline, win32file, win32event, pywintypes, winerror)
            header = self._read_exact(
                handle, 4, deadline, win32file, win32event, pywintypes, winerror
            )
            response_length = int.from_bytes(header, "big")
            if not 1 <= response_length <= MAX_FRAME_BYTES:
                raise ValueError("Invalid validator RPC response length")
            body = self._read_exact(
                handle,
                response_length,
                deadline,
                win32file,
                win32event,
                pywintypes,
                winerror,
            )
            self._write_all(
                handle,
                _RESPONSE_RECEIVED_ACK,
                deadline,
                win32file,
                win32event,
                pywintypes,
                winerror,
            )
            return header + body
        except (PermissionError, TimeoutError, ValueError):
            raise
        except Exception as failure:
            code = getattr(failure, "winerror", None)
            if code == getattr(winerror, "ERROR_SEM_TIMEOUT", 121):
                raise TimeoutError("HOLDOUT validator request timed out") from None
            raise ConnectionError("HOLDOUT validator pipe exchange failed") from None
        finally:
            if handle is not None:
                win32file.CloseHandle(handle)

    @staticmethod
    def _remaining_ms(deadline: float) -> int:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise TimeoutError("HOLDOUT validator request timed out")
        return remaining

    @classmethod
    def _write_all(cls, handle, data, deadline, win32file, win32event, pywintypes, winerror):
        offset = 0
        while offset < len(data):
            overlapped = pywintypes.OVERLAPPED()
            overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
            try:
                try:
                    error, _ = win32file.WriteFile(handle, data[offset:], overlapped)
                except win32file.error as failure:
                    if getattr(failure, "winerror", None) != winerror.ERROR_IO_PENDING:
                        raise
                    error = winerror.ERROR_IO_PENDING
                count = cls._complete_io(
                    handle, overlapped, error, deadline, win32file, win32event, winerror
                )
            finally:
                win32file.CloseHandle(overlapped.hEvent)
            if count <= 0:
                raise ConnectionError("HOLDOUT validator pipe closed")
            offset += count

    @classmethod
    def _read_exact(cls, handle, length, deadline, win32file, win32event, pywintypes, winerror):
        result = bytearray()
        while len(result) < length:
            wanted = length - len(result)
            buffer = win32file.AllocateReadBuffer(wanted)
            overlapped = pywintypes.OVERLAPPED()
            overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
            try:
                try:
                    error, _ = win32file.ReadFile(handle, buffer, overlapped)
                except win32file.error as failure:
                    if getattr(failure, "winerror", None) != winerror.ERROR_IO_PENDING:
                        raise
                    error = winerror.ERROR_IO_PENDING
                count = cls._complete_io(
                    handle, overlapped, error, deadline, win32file, win32event, winerror
                )
            finally:
                win32file.CloseHandle(overlapped.hEvent)
            if count <= 0:
                raise ConnectionError("HOLDOUT validator pipe closed")
            result.extend(buffer[:count])
        return bytes(result)

    @classmethod
    def _complete_io(cls, handle, overlapped, error, deadline, win32file, win32event, winerror):
        if error == winerror.ERROR_IO_PENDING:
            wait = win32event.WaitForSingleObject(overlapped.hEvent, cls._remaining_ms(deadline))
            if wait == win32event.WAIT_TIMEOUT:
                win32file.CancelIo(handle)
                win32event.WaitForSingleObject(overlapped.hEvent, win32event.INFINITE)
                try:
                    win32file.GetOverlappedResult(handle, overlapped, False)
                except win32file.error:
                    pass
                raise TimeoutError("HOLDOUT validator request timed out")
        return win32file.GetOverlappedResult(handle, overlapped, False)

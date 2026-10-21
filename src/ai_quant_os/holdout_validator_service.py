"""Windows named-pipe listener for the isolated HOLDOUT validator."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ai_quant_os.holdout_service_security import build_rpc_pipe_acl
from ai_quant_os.research_evidence_catalog import (
    HoldoutEvidenceStore,
    migrate_legacy_holdout_catalog,
)
from ai_quant_os.holdout_validator_rpc import (
    HOLDOUT_PIPE_NAME,
    HoldoutAggregate,
    HoldoutContextSnapshot,
    HoldoutRequest,
    HoldoutResponse,
    HoldoutScenarioAggregate,
    MAX_FRAME_BYTES,
    _RESPONSE_RECEIVED_ACK,
    process_rpc_frame,
    stress_summary_sha256,
)


def _require_service_holdout(report, holdouts) -> None:
    from ai_quant_os.research_data_parsing import parse_research_csv

    scope = report.plan.holdout_scope
    receipts = tuple(
        receipt
        for receipt in holdouts.list()
        if (
            receipt.resource_class,
            receipt.dataset_fingerprint,
            receipt.instrument_id,
            receipt.first_observed_at,
            receipt.last_observed_at,
            receipt.row_count,
        )
        == (
            "HOLDOUT",
            scope.dataset_fingerprint,
            scope.instrument_id,
            scope.first_observed_at,
            scope.last_observed_at,
            len(report.adversarial.source_bars),
        )
    )
    if len(receipts) != 1:
        raise PermissionError("Service-owned HOLDOUT receipt unavailable or ambiguous")
    archived = holdouts.get(receipts[0].dataset_ref)
    if archived is None or archived[0] != receipts[0]:
        raise PermissionError("Service-owned HOLDOUT receipt changed")
    dataset = parse_research_csv(archived[1])
    if (
        dataset.bars != report.adversarial.source_bars
        or dataset.fingerprint != scope.dataset_fingerprint
    ):
        raise PermissionError("Stress report differs from service-owned HOLDOUT bytes")


class HoldoutRequestHandler:
    """Verify retained A10/A11 evidence from archives owned by the service."""

    def __init__(self, holdouts, packages, validations, research):
        self._holdouts = holdouts
        self._packages = packages
        self._validations = validations
        self._research = research

    def __call__(self, request: HoldoutRequest) -> HoldoutResponse:
        if type(request) is not HoldoutRequest:
            raise PermissionError("Strict HOLDOUT request required")
        if request.operation == "VALIDATION_RUN":
            return self._validation(request)
        if request.operation == "VALIDATION_CONTEXT":
            return self._validation_context(request)
        if request.operation == "STRESS_RUN":
            return self._stress(request)
        if request.operation == "STRESS_CONTEXT":
            return self._stress_context(request)
        raise PermissionError("Unsupported HOLDOUT operation")

    def _checked_validation(self, request: HoldoutRequest):
        from ai_quant_os.validation_scientist_sources import validation_summary

        artifact = self._validations.get(request.evidence_ref)
        if artifact is None:
            raise PermissionError("Archived validation evidence unavailable")
        package = self._packages.get(artifact.package_ref)
        package_scope = (
            None
            if package is None
            else (
                artifact.candidate_ref,
                package.candidate.candidate_ref,
                package.training.receipt.dataset_ref,
                package.holdout.receipt.dataset_ref,
            )
        )
        request_scope = (
            (None, None, None, None)
            if request.operation == "VALIDATION_CONTEXT"
            else (
                request.candidate_ref,
                request.candidate_ref,
                request.training_ref,
                request.holdout_ref,
            )
        )
        if package is None or package_scope != request_scope:
            raise PermissionError("Validation evidence scope mismatch")
        holdout_ref = package.holdout.receipt.dataset_ref
        held = self._holdouts.get(holdout_ref)
        if held is None or held[0] != package.holdout.receipt:
            raise PermissionError("Archived HOLDOUT receipt mismatch")
        from ai_quant_os.validation_package_contracts import checked_bytes

        package_bytes = checked_bytes(
            package.holdout.raw_base64, package.holdout.receipt.raw_sha256, 1048576
        )
        if held[1] != package_bytes:
            raise PermissionError("Archived HOLDOUT bytes mismatch")
        return package, artifact, validation_summary(package, artifact)

    def _validation(self, request: HoldoutRequest) -> HoldoutResponse:
        _package, _artifact, summary = self._checked_validation(request)
        return HoldoutResponse(
            request_id=request.request_id,
            task_id=request.task_id,
            context_ref=request.context_ref,
            status="CHECKED_ARCHIVED_OBSERVATION",
            evidence_ref=request.evidence_ref,
            summary_sha256=summary.summary_sha256,
            aggregate=HoldoutAggregate(outcome=summary.outcome, gate_count=len(summary.gates)),
        )

    def _validation_context(self, request: HoldoutRequest) -> HoldoutResponse:
        from ai_quant_os.validation_scientist_sources import ValidationContextSnapshot

        package, _artifact, summary = self._checked_validation(request)
        snapshot = ValidationContextSnapshot(
            summary=summary,
            holdout_receipt=package.holdout.receipt,
            captured_at=package.captured_at,
        )
        return HoldoutResponse(
            request_id=request.request_id,
            task_id=request.task_id,
            context_ref=request.context_ref,
            status="CHECKED_ARCHIVED_VALIDATION_CONTEXT",
            evidence_ref=request.evidence_ref,
            summary_sha256=summary.summary_sha256,
            aggregate=HoldoutAggregate(outcome=summary.outcome, gate_count=len(summary.gates)),
            context_snapshot=HoldoutContextSnapshot(
                agent_id="A10", payload_json=snapshot.model_dump_json()
            ),
        )

    def _checked_stress(self, request: HoldoutRequest):
        from hashlib import sha256

        from ai_quant_os.research_adversarial import verify_adversarial
        from ai_quant_os.research_results import ResearchReport
        from ai_quant_os.research_store import encode_research

        experiment_id = (
            request.evidence_ref if request.operation == "STRESS_CONTEXT" else request.candidate_ref
        )
        report = self._research.get(experiment_id)
        if type(report) is not ResearchReport or report.schema_version != "1.2":
            raise PermissionError("Archived stress evidence unavailable")
        digest = sha256(encode_research(report)).hexdigest()
        expected_evidence = f"research-report:{report.plan.experiment_id}:{digest}"
        context_request = request.operation == "STRESS_CONTEXT"
        if context_request:
            scope_matches = (
                request.candidate_ref is None
                and request.training_ref is None
                and request.holdout_ref is None
                and request.evidence_ref == report.plan.experiment_id
            )
        else:
            scope_matches = (
                request.candidate_ref == report.plan.experiment_id
                and request.evidence_ref == expected_evidence
                and request.training_ref
                == "research-data-scope:" + report.plan.development_fingerprint
                and request.holdout_ref == "research-data-scope:" + report.plan.holdout_fingerprint
            )
        if not scope_matches:
            raise PermissionError("Archived stress scope mismatch")
        if report.adversarial is None:
            raise PermissionError("Archived adversarial source evidence unavailable")
        _require_service_holdout(report, self._holdouts)
        verify_adversarial(report.adversarial, report.plan, report.holdout, report.attempts)
        scenarios = tuple(
            HoldoutScenarioAggregate(
                name=row.name,
                dataset_fingerprint=row.dataset_fingerprint,
                net_pnl=row.run.result.net_pnl,
                pnl_currency=row.run.result.pnl_currency,
                native_order_count=row.run.result.native_order_count,
            )
            for row in report.adversarial.scenarios
        )
        reason = report.adversarial.reason
        return report, digest, scenarios, reason

    def _stress(self, request: HoldoutRequest) -> HoldoutResponse:
        report, digest, scenarios, reason = self._checked_stress(request)
        return HoldoutResponse(
            request_id=request.request_id,
            task_id=request.task_id,
            context_ref=request.context_ref,
            status="CHECKED_ARCHIVED_STRESS",
            evidence_ref=f"research-report:{report.plan.experiment_id}:{digest}",
            summary_sha256=stress_summary_sha256(report.adversarial.state, reason, scenarios),
            aggregate=HoldoutAggregate(
                state=report.adversarial.state,
                scenario_count=len(scenarios),
                reason_codes=(reason,),
                scenarios=scenarios,
            ),
        )

    def _stress_context(self, request: HoldoutRequest) -> HoldoutResponse:
        from ai_quant_os.adversarial_agent import adversarial_context_snapshot

        report, digest, scenarios, reason = self._checked_stress(request)
        snapshot = adversarial_context_snapshot(report, now=datetime.now(timezone.utc))
        if snapshot.candidate.report_sha256 != digest:
            raise PermissionError("Stress context report digest changed")
        return HoldoutResponse(
            request_id=request.request_id,
            task_id=request.task_id,
            context_ref=request.context_ref,
            status="CHECKED_ARCHIVED_STRESS_CONTEXT",
            evidence_ref=request.evidence_ref,
            summary_sha256=stress_summary_sha256(report.adversarial.state, reason, scenarios),
            aggregate=HoldoutAggregate(
                state=report.adversarial.state,
                scenario_count=len(scenarios),
                reason_codes=(reason,),
                scenarios=scenarios,
            ),
            context_snapshot=HoldoutContextSnapshot(
                agent_id="A11", payload_json=snapshot.model_dump_json()
            ),
        )


_SERVICE_SID_PATTERN = re.compile(r"^S-1-5-80-(?:\d+-){4}\d+$")
_SID_PATTERN = re.compile(r"^S-1-\d+(?:-\d+){1,15}$")


def _open_service_holdout_store(data_dir: Path) -> HoldoutEvidenceStore:
    """Open the service-owned HOLDOUT partition without creating or writing files."""
    return HoldoutEvidenceStore(Path(data_dir) / "research-data.holdout.sqlite3")


def stage_service_archives(
    data_dir: Path,
    *,
    holdout_source: Path,
    validation_packages_source: Path,
    candidate_validations_source: Path,
    research_source: Path,
) -> dict[str, str]:
    """Copy verified archives into an empty service directory without changing sources."""
    from ai_quant_os.candidate_validation_store import CandidateValidationStore
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.validation_package_store import ValidationPackageStore

    root = Path(data_dir)
    if not root.is_absolute():
        raise ValueError("HOLDOUT service data directory must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("HOLDOUT service data directory must be a real directory")
    if any(root.iterdir()):
        raise FileExistsError("HOLDOUT service data directory must be empty before staging")
    sources = {
        "research-data.holdout.sqlite3": (Path(holdout_source), HoldoutEvidenceStore),
        "validation-packages.sqlite3": (Path(validation_packages_source), ValidationPackageStore),
        "candidate-validations.sqlite3": (
            Path(candidate_validations_source),
            CandidateValidationStore,
        ),
        "research.sqlite3": (Path(research_source), ResearchStore),
    }
    destinations = {name: root / name for name in sources}
    if any(path.exists() or path.is_symlink() for path in destinations.values()):
        raise FileExistsError("HOLDOUT service archive destination already exists")

    def validate_archive(path: Path, store_type) -> None:
        if store_type is ResearchStore:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Research archive integrity check failed")
                tables = {
                    row[0]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                required = {
                    "research_hypotheses",
                    "research_experiments",
                    "research_holdout_claims",
                }
                if not required <= tables:
                    raise ValueError("Research archive schema is incomplete")
                experiment_ids = tuple(
                    row[0] for row in db.execute("SELECT experiment_id FROM research_experiments")
                )
                verifier = object.__new__(ResearchStore)
                for experiment_id in experiment_ids:
                    verifier._report(verifier._row(db, experiment_id))
            return
        store = store_type(path)
        close = getattr(store, "close", None)
        if close is not None:
            close()

    def validate_source(path: Path, store_type) -> None:
        if store_type is HoldoutEvidenceStore:
            validate_archive(path, store_type)
            return
        if store_type is ResearchStore:
            validate_archive(path, store_type)
            return
        required_tables = (
            {"validation_packages", "audit_events", "audit_chain", "audit_head"}
            if store_type is ValidationPackageStore
            else {"candidate_validations", "audit_events", "audit_chain", "audit_head"}
        )
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Source archive integrity check failed")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if (
                not required_tables <= tables
                or db.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0] != 1
            ):
                raise ValueError("Source archive metadata is incomplete")

    staged: list[Path] = []
    try:
        for name, (source, store_type) in sources.items():
            try:
                metadata = os.lstat(source)
            except FileNotFoundError:
                raise FileNotFoundError("Required source archive is unavailable") from None
            if (
                not source.is_absolute()
                or not source.is_file()
                or source.is_symlink()
                or getattr(metadata, "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("Source archive must be an absolute regular file")
            validate_source(source, store_type)
            descriptor, temp_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".stage", dir=root)
            os.close(descriptor)
            temporary = Path(temp_name)
            try:
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as source_db:
                    with closing(sqlite3.connect(temporary)) as destination_db:
                        source_db.backup(destination_db)
                validate_archive(temporary, store_type)
                os.link(temporary, destinations[name])
                staged.append(destinations[name])
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
        return {name: str(path) for name, path in destinations.items()}
    except BaseException:
        for path in staged:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise


class WindowsNamedPipeServer:
    """Single-connection-at-a-time server with an ACL-scoped pipe and bounded I/O."""

    def __init__(
        self,
        service_sid: str,
        operator_sid: str,
        *,
        pipe_name: str = HOLDOUT_PIPE_NAME,
        io_timeout_ms: int = 5000,
        failure_reporter: Callable[[str], None] | None = None,
        one_shot: bool = False,
    ) -> None:
        if not _SERVICE_SID_PATTERN.fullmatch(service_sid):
            raise ValueError("Invalid validator service SID")
        if not _SID_PATTERN.fullmatch(operator_sid) or operator_sid in {service_sid, "S-1-5-18"}:
            raise ValueError("Invalid validator operator SID")
        if not pipe_name.startswith(r"\\.\pipe\AIQuantHoldout.") or ".." in pipe_name:
            raise ValueError("Invalid validator RPC pipe name")
        if type(io_timeout_ms) is not int or not 50 <= io_timeout_ms <= 30000:
            raise ValueError("Invalid validator server deadline")
        self._service_sid = service_sid
        self._operator_sid = operator_sid
        self._pipe_name = pipe_name
        self._io_timeout_ms = io_timeout_ms
        self._failure_reporter = failure_reporter
        self._one_shot = one_shot

    def serve(self, stop_event, handler: Callable) -> None:
        if os.name != "nt":
            raise OSError("Validator service requires Windows")
        import pywintypes
        import win32event
        import win32file
        import win32pipe
        import winerror
        import win32security

        while win32event.WaitForSingleObject(stop_event, 0) != win32event.WAIT_OBJECT_0:
            attributes = pywintypes.SECURITY_ATTRIBUTES()
            acl = build_rpc_pipe_acl(
                self._service_sid,
                self._operator_sid,
                security=win32security,
            )
            attributes.SetSecurityDescriptorDacl(True, acl, False)
            pipe = win32pipe.CreateNamedPipe(
                self._pipe_name,
                win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
                1,
                MAX_FRAME_BYTES + 4,
                MAX_FRAME_BYTES + 4,
                self._io_timeout_ms,
                attributes,
            )
            stage = "ACCEPT"
            served = False
            try:
                if not self._accept(
                    pipe, stop_event, win32pipe, win32event, win32file, pywintypes, winerror
                ):
                    break
                deadline = time.monotonic() + self._io_timeout_ms / 1000
                request_frame = self._read_frame(
                    pipe,
                    stop_event,
                    deadline,
                    win32file,
                    win32event,
                    pywintypes,
                    winerror,
                )
                stage = "PROCESS"
                response = process_rpc_frame(request_frame, handler)
                if response is not None:
                    stage = "WRITE"
                    self._write_all(
                        pipe,
                        response,
                        stop_event,
                        deadline,
                        win32file,
                        win32event,
                        pywintypes,
                        winerror,
                    )
                    stage = "ACK_READ"
                    acknowledgment = self._read_exact(
                        pipe,
                        len(_RESPONSE_RECEIVED_ACK),
                        stop_event,
                        deadline,
                        win32file,
                        win32event,
                        pywintypes,
                        winerror,
                    )
                    stage = "ACK_VALIDATE"
                    if acknowledgment != _RESPONSE_RECEIVED_ACK:
                        raise ValueError("Invalid validator response acknowledgment")
                    served = True
            except TimeoutError:
                self._report_failure("TIMEOUT")
                pass
            except Exception:
                # Do not log exception text or request contents from this boundary.
                self._report_failure(stage + "_FAILURE")
                pass
            finally:
                try:
                    win32pipe.DisconnectNamedPipe(pipe)
                except win32file.error:
                    pass
                win32file.CloseHandle(pipe)
            if served and self._one_shot:
                break

    def _report_failure(self, category: str) -> None:
        if self._failure_reporter is not None:
            try:
                self._failure_reporter(category)
            except Exception:
                # Diagnostics must never change pipe cleanup or service availability.
                pass

    @staticmethod
    def _accept(pipe, stop_event, win32pipe, win32event, win32file, pywintypes, winerror):
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        try:
            try:
                result = win32pipe.ConnectNamedPipe(pipe, overlapped)
                pending = result == winerror.ERROR_IO_PENDING
            except win32file.error as error:
                code = getattr(error, "winerror", None)
                if code == winerror.ERROR_PIPE_CONNECTED:
                    win32event.SetEvent(overlapped.hEvent)
                    pending = False
                elif code == winerror.ERROR_IO_PENDING:
                    pending = True
                else:
                    raise
            if pending and not WindowsNamedPipeServer._wait_io(
                pipe,
                overlapped,
                stop_event,
                None,
                win32file,
                win32event,
                winerror,
            ):
                return False
            if pending:
                win32file.GetOverlappedResult(pipe, overlapped, False)
            return True
        finally:
            win32file.CloseHandle(overlapped.hEvent)

    @staticmethod
    def _wait_io(pipe, overlapped, stop_event, deadline, win32file, win32event, winerror):
        while True:
            remaining = 250 if deadline is None else int((deadline - time.monotonic()) * 1000)
            if deadline is not None and remaining <= 0:
                win32file.CancelIo(pipe)
                win32event.WaitForSingleObject(overlapped.hEvent, win32event.INFINITE)
                try:
                    win32file.GetOverlappedResult(pipe, overlapped, False)
                except win32file.error:
                    pass
                raise TimeoutError("HOLDOUT validator service deadline exceeded")
            result = win32event.WaitForMultipleObjects(
                (stop_event, overlapped.hEvent), False, min(remaining, 250)
            )
            if result == win32event.WAIT_OBJECT_0:
                win32file.CancelIo(pipe)
                win32event.WaitForSingleObject(overlapped.hEvent, win32event.INFINITE)
                return False
            if result == win32event.WAIT_OBJECT_0 + 1:
                return True

    @classmethod
    def _read_frame(cls, pipe, stop_event, deadline, win32file, win32event, pywintypes, winerror):
        header = cls._read_exact(
            pipe, 4, stop_event, deadline, win32file, win32event, pywintypes, winerror
        )
        size = int.from_bytes(header, "big")
        if not 1 <= size <= MAX_FRAME_BYTES:
            raise ValueError("Invalid HOLDOUT RPC frame length")
        return header + cls._read_exact(
            pipe, size, stop_event, deadline, win32file, win32event, pywintypes, winerror
        )

    @classmethod
    def _read_exact(
        cls, pipe, length, stop_event, deadline, win32file, win32event, pywintypes, winerror
    ):
        result = bytearray()
        while len(result) < length:
            buffer = win32file.AllocateReadBuffer(length - len(result))
            overlapped = pywintypes.OVERLAPPED()
            overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
            try:
                try:
                    error, _ = win32file.ReadFile(pipe, buffer, overlapped)
                    pending = error == winerror.ERROR_IO_PENDING
                except win32file.error as exception:
                    if getattr(exception, "winerror", None) != winerror.ERROR_IO_PENDING:
                        raise
                    pending = True
                if pending and not cls._wait_io(
                    pipe, overlapped, stop_event, deadline, win32file, win32event, winerror
                ):
                    raise InterruptedError("HOLDOUT validator is stopping")
                count = win32file.GetOverlappedResult(pipe, overlapped, False)
            finally:
                win32file.CloseHandle(overlapped.hEvent)
            if count <= 0:
                raise ConnectionError("HOLDOUT validator client disconnected")
            result.extend(buffer[:count])
        return bytes(result)

    @classmethod
    def _write_all(
        cls, pipe, data, stop_event, deadline, win32file, win32event, pywintypes, winerror
    ):
        offset = 0
        while offset < len(data):
            overlapped = pywintypes.OVERLAPPED()
            overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
            try:
                try:
                    error, _ = win32file.WriteFile(pipe, data[offset:], overlapped)
                    pending = error == winerror.ERROR_IO_PENDING
                except win32file.error as exception:
                    if getattr(exception, "winerror", None) != winerror.ERROR_IO_PENDING:
                        raise
                    pending = True
                if pending and not cls._wait_io(
                    pipe, overlapped, stop_event, deadline, win32file, win32event, winerror
                ):
                    raise InterruptedError("HOLDOUT validator is stopping")
                count = win32file.GetOverlappedResult(pipe, overlapped, False)
            finally:
                win32file.CloseHandle(overlapped.hEvent)
            if count <= 0:
                raise ConnectionError("HOLDOUT validator client disconnected")
            offset += count


_SERVICE_REGISTRY_KEY = r"SOFTWARE\AIQuantOS\HoldoutValidator"


def _read_service_settings() -> tuple[Path, str]:
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _SERVICE_REGISTRY_KEY) as key:
        data_dir, _ = winreg.QueryValueEx(key, "DataDirectory")
        operator_sid, _ = winreg.QueryValueEx(key, "OperatorSid")
    path = Path(data_dir)
    if not path.is_absolute() or not operator_sid.startswith("S-1-"):
        raise ValueError("Invalid HOLDOUT service configuration")
    return path, operator_sid


def _write_service_settings(data_dir: Path, operator_sid: str) -> None:
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE,
        _SERVICE_REGISTRY_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "DataDirectory", 0, winreg.REG_SZ, str(data_dir))
        winreg.SetValueEx(key, "OperatorSid", 0, winreg.REG_SZ, operator_sid)


def _ensure_service_registered(service_api, service_util) -> None:
    manager = service_api.OpenSCManager(None, None, service_api.SC_MANAGER_CONNECT)
    service = None
    try:
        try:
            service = service_api.OpenService(
                manager, "AIQuantHoldout", service_api.SERVICE_QUERY_STATUS
            )
        except Exception as error:
            missing_service = getattr(service_api, "ERROR_SERVICE_DOES_NOT_EXIST", 1060)
            if getattr(error, "winerror", None) != missing_service:
                raise
        service_class = "ai_quant_os.holdout_validator_service.HoldoutValidatorService"
        options = {
            "startType": service_api.SERVICE_AUTO_START,
            "userName": "NT AUTHORITY\\LocalService",
            "description": "Isolated local PAPER validation service",
        }
        if service is None:
            service_util.InstallService(
                service_class,
                "AIQuantHoldout",
                "AI Quant HOLDOUT Validator",
                **options,
            )
        else:
            if service_api.QueryServiceStatus(service)[1] != service_api.SERVICE_STOPPED:
                service_util.StopService("AIQuantHoldout")
            service_util.ChangeServiceConfig(service_class, "AIQuantHoldout", **options)
    finally:
        if service is not None:
            service_api.CloseServiceHandle(service)
        service_api.CloseServiceHandle(manager)


if os.name == "nt":
    import servicemanager
    import win32api
    import win32event
    import win32security
    import win32service
    import win32serviceutil
    import winerror

    from ai_quant_os.holdout_service_security import (
        HoldoutDirectoryAcl,
        configure_holdout_service_sid,
        current_process_user_sid,
        secure_holdout_directory,
        service_sid_for_name,
        verify_holdout_directory_tree,
    )

    class HoldoutValidatorService(win32serviceutil.ServiceFramework):
        _svc_name_ = "AIQuantHoldout"
        _svc_display_name_ = "AI Quant HOLDOUT Validator"
        _svc_description_ = "Isolated local validation and stress service for PAPER research"

        def __init__(self, args):
            super().__init__(args)
            self._stop_event = win32event.CreateEvent(None, True, False, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._stop_event)

        def SvcShutdown(self):
            self.SvcStop()

        def SvcDoRun(self):
            try:
                data_dir, operator_sid = _read_service_settings()
                service_sid = service_sid_for_name(self._svc_name_, security=win32security)
                token = win32security.OpenProcessToken(
                    win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
                )
                try:
                    groups = win32security.GetTokenInformation(token, win32security.TokenGroups)
                    if not any(
                        win32security.ConvertSidToStringSid(sid) == service_sid
                        and attributes & win32security.SE_GROUP_ENABLED
                        for sid, attributes in groups
                    ):
                        raise PermissionError("HOLDOUT service SID missing from process token")
                finally:
                    win32api.CloseHandle(token)
                verify_holdout_directory_tree(
                    data_dir,
                    HoldoutDirectoryAcl(service_sid),
                    security=win32security,
                )
                holdout_store = _open_service_holdout_store(data_dir)
                archive_paths = {
                    "packages": data_dir / "validation-packages.sqlite3",
                    "validations": data_dir / "candidate-validations.sqlite3",
                    "research": data_dir / "research.sqlite3",
                }
                if any(not path.is_file() for path in archive_paths.values()):
                    holdout_store.close()
                    raise FileNotFoundError("HOLDOUT service archives are not staged")
                from ai_quant_os.candidate_validation_store import CandidateValidationStore
                from ai_quant_os.research_store import ResearchStore
                from ai_quant_os.validation_package_store import ValidationPackageStore

                packages = ValidationPackageStore(archive_paths["packages"])
                validations = CandidateValidationStore(archive_paths["validations"])
                research = ResearchStore(archive_paths["research"])
                self.ReportServiceStatus(win32service.SERVICE_RUNNING)
                server = WindowsNamedPipeServer(service_sid, operator_sid)
                try:
                    server.serve(
                        self._stop_event,
                        HoldoutRequestHandler(holdout_store, packages, validations, research),
                    )
                finally:
                    validations.close()
                    packages.close()
                    holdout_store.close()
            except Exception:
                servicemanager.LogErrorMsg("HOLDOUT validator startup or runtime failure")
                self.ReportServiceStatus(
                    win32service.SERVICE_STOPPED,
                    win32ExitCode=winerror.ERROR_SERVICE_SPECIFIC_ERROR,
                    svcExitCode=1,
                )
                return
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)

else:

    class HoldoutValidatorService:
        """Importable stub so non-Windows test and tooling can inspect the package."""


def _install_service(data_dir: Path, *, paper_db: Path, research_workspace: Path) -> None:
    if os.name != "nt":
        raise OSError("HOLDOUT validator service requires Windows")
    import win32service
    import win32serviceutil

    data_dir = Path(data_dir)
    if not data_dir.is_absolute():
        raise ValueError("HOLDOUT data directory must be absolute")
    paper_db = Path(paper_db)
    research_workspace = Path(research_workspace)
    if not paper_db.is_absolute() or not research_workspace.is_absolute():
        raise ValueError("PAPER and research source paths must be absolute")
    operator_sid = current_process_user_sid()
    _ensure_service_registered(win32service, win32serviceutil)
    try:
        configure_holdout_service_sid(service_api=win32service)
        archive_names = (
            "research-data.holdout.sqlite3",
            "validation-packages.sqlite3",
            "candidate-validations.sqlite3",
            "research.sqlite3",
        )
        present = tuple((data_dir / name).is_file() for name in archive_names)
        if all(present):
            adopt_existing = False
        elif any(present):
            raise FileExistsError("HOLDOUT service archive set is incomplete")
        else:
            stage_service_archives(
                data_dir,
                holdout_source=paper_db.with_name(paper_db.stem + ".research-data.holdout.sqlite3"),
                validation_packages_source=paper_db.with_name(
                    paper_db.stem + ".validation-packages.sqlite3"
                ),
                candidate_validations_source=paper_db.with_name(
                    paper_db.stem + ".candidate-validations.sqlite3"
                ),
                research_source=research_workspace / "research.sqlite3",
            )
            adopt_existing = True
        _write_service_settings(data_dir, operator_sid)
        secure_holdout_directory(data_dir, adopt_existing=adopt_existing)
        win32serviceutil.StartService("AIQuantHoldout")
    except Exception:
        # Keep the installed service and source data intact so install can be retried.
        # The service is not started until identity, settings, and ACL checks succeed.
        raise


def _remove_service(*, service_api=None, service_util=None) -> None:
    if service_api is None or service_util is None:
        if os.name != "nt":
            raise OSError("HOLDOUT validator service requires Windows")
        import win32service
        import win32serviceutil

        service_api = service_api or win32service
        service_util = service_util or win32serviceutil

    manager = service_api.OpenSCManager(None, None, service_api.SC_MANAGER_CONNECT)
    service = None
    try:
        try:
            service = service_api.OpenService(
                manager, "AIQuantHoldout", service_api.SERVICE_QUERY_STATUS
            )
        except Exception as error:
            missing_service = getattr(service_api, "ERROR_SERVICE_DOES_NOT_EXIST", 1060)
            if getattr(error, "winerror", None) == missing_service:
                return
            raise
        state = service_api.QueryServiceStatus(service)[1]
    finally:
        if service is not None:
            service_api.CloseServiceHandle(service)
        service_api.CloseServiceHandle(manager)
    if state != service_api.SERVICE_STOPPED:
        service_util.StopService("AIQuantHoldout")
    service_util.RemoveService("AIQuantHoldout")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--data-dir", required=True, type=Path)
    install.add_argument("--paper-db", required=True, type=Path)
    install.add_argument("--research-workspace", required=True, type=Path)
    migrate = commands.add_parser("migrate-legacy-catalog")
    migrate.add_argument("--catalog", required=True, type=Path)
    commands.add_parser("remove")
    args = parser.parse_args(argv)
    if args.command != "migrate-legacy-catalog" and os.name != "nt":
        parser.error("The HOLDOUT validator service is Windows-only")
    try:
        if args.command == "migrate-legacy-catalog":
            holdouts = migrate_legacy_holdout_catalog(args.catalog)
            result = {
                "catalog": str(args.catalog),
                "holdout_receipts_migrated": len(holdouts),
                "state": "OFFLINE_MIGRATION_COMPLETE",
                "acl_secured": False,
            }
        elif args.command == "install":
            _install_service(
                args.data_dir,
                paper_db=args.paper_db,
                research_workspace=args.research_workspace,
            )
            result = {"service": "AIQuantHoldout", "state": "INSTALLED_AND_STARTED"}
        else:
            _remove_service()
            result = {"service": "AIQuantHoldout", "state": "REMOVED_DATA_PRESERVED"}
    except Exception:
        parser.exit(2, "HOLDOUT validator service operation failed; data was preserved.\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

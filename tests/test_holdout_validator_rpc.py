import json
import os
import threading
import uuid
from decimal import Decimal
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ai_quant_os.holdout_validator_rpc import (
    HOLDOUT_PIPE_NAME,
    MAX_FRAME_BYTES,
    HoldoutRequest,
    HoldoutResponse,
    HoldoutContextSnapshot,
    HoldoutServiceFailure,
    HoldoutRpcClient,
    WindowsNamedPipeTransport,
    decode_frame,
    encode_frame,
    process_rpc_frame,
    verify_response,
)


def request(**changes):
    value = {
        "request_id": "request-001",
        "operation": "VALIDATION_RUN",
        "agent_id": "A10",
        "agent_version": "0.2.0",
        "task_id": "task-001",
        "context_ref": "context-001",
        "candidate_ref": "candidate-001",
        "training_ref": "training-001",
        "holdout_ref": "holdout-001",
        "evidence_ref": "validation-summary-001",
    }
    value.update(changes)
    return value


def test_request_is_strictly_bound_to_validation_identity_and_refs():
    assert HOLDOUT_PIPE_NAME.endswith(".v4")
    parsed = HoldoutRequest.model_validate(request())
    assert parsed.operation == "VALIDATION_RUN"
    assert parsed.holdout_ref == "holdout-001"

    for invalid in (
        request(**{"agent_id": "A01"}),
        request(**{"operation": "STRESS_RUN"}),
        request(**{"payload": {"raw_bars": "secret"}}),
        request(**{"holdout_ref": ""}),
    ):
        with pytest.raises(ValidationError):
            HoldoutRequest.model_validate(invalid)


def test_context_request_omits_unresolved_archive_refs_and_requires_snapshot_reply():
    context_request = HoldoutRequest.model_validate(
        request(
            operation="VALIDATION_CONTEXT", candidate_ref=None, training_ref=None, holdout_ref=None
        )
    )
    response = HoldoutResponse(
        request_id=context_request.request_id,
        task_id=context_request.task_id,
        context_ref=context_request.context_ref,
        status="CHECKED_ARCHIVED_VALIDATION_CONTEXT",
        evidence_ref=context_request.evidence_ref,
        summary_sha256="a" * 64,
        aggregate={"outcome": "REJECTED", "gate_count": 15},
        context_snapshot=HoldoutContextSnapshot(agent_id="A10", payload_json="{}"),
    )
    assert verify_response(context_request, response) == response

    with pytest.raises(ValidationError):
        HoldoutRequest.model_validate(
            request(operation="VALIDATION_CONTEXT", candidate_ref="candidate-001")
        )
    with pytest.raises(ValueError, match="snapshot"):
        verify_response(context_request, response.model_copy(update={"context_snapshot": None}))


def test_validation_package_preparation_request_can_transfer_only_checked_inputs_to_service():
    import base64

    raw_training = b"training snapshot" * 5000
    payload = {
        "graph_json": "{}",
        "candidate_json": "{}",
        "training_receipt_json": "{}",
        "training_raw_base64": base64.b64encode(raw_training).decode(),
        "plan_json": "{}",
        "captured_at": datetime(2026, 10, 4, 12, tzinfo=timezone.utc),
    }
    prepared = HoldoutRequest.model_validate(
        request(
            operation="VALIDATION_PREPARE",
            evidence_ref="build-001",
            preparation=payload,
        )
    )

    encoded = encode_frame(prepared)
    assert len(encoded) > 65536
    assert decode_frame(encoded, HoldoutRequest) == prepared

    with pytest.raises(ValidationError):
        HoldoutRequest.model_validate(request(operation="VALIDATION_RUN", preparation=payload))


def test_validation_publication_request_is_bounded_and_has_no_caller_supplied_archive_scope():
    from ai_quant_os.holdout_validator_rpc import HoldoutValidationPublication

    publication = HoldoutValidationPublication(
        kind="PREREQUISITE_FAILURE",
        package_ref="validation-package:" + "a" * 64,
        test_result_json='{"status":"PROPOSED_TESTS_FAILED_UNATTESTED"}',
    )
    payload = request(
        operation="VALIDATION_PUBLISH",
        candidate_ref=None,
        training_ref=None,
        holdout_ref=None,
        evidence_ref=publication.package_ref,
        publication=publication,
    )
    parsed = HoldoutRequest.model_validate(payload)
    assert decode_frame(encode_frame(parsed), HoldoutRequest) == parsed
    assert len(encode_frame(parsed)) <= MAX_FRAME_BYTES + 4

    for invalid in (
        {**payload, "agent_id": "A11"},
        {**payload, "evidence_ref": "validation-package:" + "b" * 64},
        {**payload, "candidate_ref": "caller-controlled-candidate"},
    ):
        with pytest.raises(ValidationError):
            HoldoutRequest.model_validate(invalid)

    with pytest.raises(ValidationError):
        HoldoutValidationPublication(
            kind="PREREQUISITE_FAILURE",
            package_ref=publication.package_ref,
            test_result_json="x" * (MAX_FRAME_BYTES + 1),
        )


def test_holdout_ingestion_request_binds_bytes_and_training_inventory():
    import base64
    from hashlib import sha256

    from ai_quant_os.holdout_validator_rpc import HoldoutDatasetPublication

    raw = b"checked holdout csv"
    publication = HoldoutDatasetPublication(
        source_id="sealed-feed",
        raw_base64=base64.b64encode(raw).decode(),
        raw_sha256=sha256(raw).hexdigest(),
        training_receipts_json=(),
    )
    payload = request(
        operation="HOLDOUT_PUBLISH",
        candidate_ref=None,
        training_ref=None,
        holdout_ref=None,
        evidence_ref="holdout-ingest:" + publication.raw_sha256,
        holdout_publication=publication,
    )
    parsed = HoldoutRequest.model_validate(payload)
    assert decode_frame(encode_frame(parsed), HoldoutRequest) == parsed
    assert len(encode_frame(parsed)) <= MAX_FRAME_BYTES + 4

    for invalid in (
        {**payload, "agent_id": "A11"},
        {**payload, "evidence_ref": "holdout-ingest:" + "0" * 64},
        {**payload, "candidate_ref": "candidate-001"},
    ):
        with pytest.raises(ValidationError):
            HoldoutRequest.model_validate(invalid)
    with pytest.raises(ValidationError):
        HoldoutDatasetPublication(
            source_id="sealed-feed",
            raw_base64=base64.b64encode(raw).decode(),
            raw_sha256="0" * 64,
            training_receipts_json=(),
        )

    from hashlib import sha256

    from ai_quant_os.research_evidence_catalog import _receipt
    from test_research_evidence_catalog import NOW, raw_bars

    raw_csv = raw_bars(minute=10)
    uploaded = HoldoutDatasetPublication(
        source_id="sealed-feed",
        raw_base64=base64.b64encode(raw_csv).decode(),
        raw_sha256=sha256(raw_csv).hexdigest(),
        training_receipts_json=(),
    )
    ingestion = HoldoutRequest.model_validate(
        request(
            operation="HOLDOUT_PUBLISH",
            candidate_ref=None,
            training_ref=None,
            holdout_ref=None,
            evidence_ref="holdout-ingest:" + uploaded.raw_sha256,
            holdout_publication=uploaded,
        )
    )
    receipt_json = _receipt(raw_csv, "sealed-feed", "HOLDOUT", NOW).model_dump_json()
    response = HoldoutResponse(
        request_id=ingestion.request_id,
        task_id=ingestion.task_id,
        context_ref=ingestion.context_ref,
        status="CHECKED_HOLDOUT_PUBLISHED",
        evidence_ref=ingestion.evidence_ref,
        summary_sha256=sha256(receipt_json.encode()).hexdigest(),
        aggregate={},
        published_ref=_receipt(raw_csv, "sealed-feed", "HOLDOUT", NOW).dataset_ref,
        receipt_json=receipt_json,
    )
    assert verify_response(ingestion, response) == response
    with pytest.raises(ValueError, match="receipt binding"):
        verify_response(ingestion, response.model_copy(update={"receipt_json": "{}"}))


def test_installed_client_fails_closed_off_windows(monkeypatch):
    from ai_quant_os import holdout_validator_rpc as rpc

    monkeypatch.setattr(rpc.os, "name", "posix")
    client = rpc.installed_windows_holdout_client()
    with pytest.raises(ConnectionError, match="service is unavailable"):
        client.call(HoldoutRequest.model_validate(request()))


def test_installed_client_fails_closed_when_service_identity_is_unregistered(monkeypatch):
    from ai_quant_os import holdout_service_security, holdout_validator_rpc as rpc

    monkeypatch.setattr(rpc.os, "name", "nt")
    monkeypatch.setattr(
        holdout_service_security,
        "service_sid_for_name",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("not installed")),
    )
    client = rpc.installed_windows_holdout_client()
    with pytest.raises(ConnectionError, match="service is unavailable"):
        client.call(HoldoutRequest.model_validate(request()))


def test_response_exposes_only_checked_aggregate_and_receipt():
    response = HoldoutResponse.model_validate_json(
        json.dumps(
            {
                "request_id": "request-001",
                "task_id": "task-001",
                "context_ref": "context-001",
                "status": "CHECKED_ARCHIVED_OBSERVATION",
                "evidence_ref": "validation-summary-001",
                "summary_sha256": "a" * 64,
                "aggregate": {"outcome": "REJECTED", "gate_count": 5},
            }
        )
    )
    assert response.aggregate.outcome == "REJECTED"
    verify_response(HoldoutRequest.model_validate(request()), response)
    with pytest.raises(ValueError, match="identity"):
        verify_response(
            HoldoutRequest.model_validate(request(**{"task_id": "different-task"})),
            response,
        )
    with pytest.raises(ValueError, match="evidence"):
        verify_response(
            HoldoutRequest.model_validate(request()),
            response.model_copy(update={"evidence_ref": "candidate-validation:" + "0" * 64}),
        )


def test_stress_response_carries_only_bounded_scenario_aggregates():
    request_model = HoldoutRequest.model_validate(
        request(
            operation="STRESS_RUN",
            agent_id="A11",
            evidence_ref="research-report:experiment-001",
        )
    )
    response = HoldoutResponse.model_validate_json(
        json.dumps(
            {
                "request_id": "request-001",
                "task_id": "task-001",
                "context_ref": "context-001",
                "status": "CHECKED_ARCHIVED_STRESS",
                "evidence_ref": "research-report:experiment-001",
                "summary_sha256": "d" * 64,
                "aggregate": {
                    "state": "FAIL",
                    "scenario_count": 1,
                    "reason_codes": ["DECLARED_SCENARIO_LOSS_EXCEEDED"],
                    "scenarios": [
                        {
                            "name": "cost_stress",
                            "dataset_fingerprint": "e" * 64,
                            "net_pnl": "-12.50",
                            "pnl_currency": "USD",
                            "native_order_count": 3,
                        }
                    ],
                },
            }
        )
    )
    assert verify_response(request_model, response).aggregate.scenarios[0].net_pnl == Decimal(
        "-12.50"
    )
    with pytest.raises(ValidationError):
        HoldoutResponse.model_validate(
            {
                **response.model_dump(),
                "aggregate": {
                    **response.aggregate.model_dump(),
                    "scenario_count": 2,
                },
            }
        )
    with pytest.raises(ValidationError):
        HoldoutResponse.model_validate(
            {
                **response.model_dump(),
                "raw_bars": "private",
            }
        )


def test_rpc_frames_are_json_bounded_and_reject_trailing_or_oversized_data():
    encoded = encode_frame(HoldoutRequest.model_validate(request()))
    assert len(encoded) <= MAX_FRAME_BYTES
    assert decode_frame(encoded, HoldoutRequest).request_id == "request-001"

    with pytest.raises(ValueError, match="length"):
        decode_frame(b"\x00\x00\x00\x05{}", HoldoutRequest)
    with pytest.raises(ValueError, match="bound"):
        encode_frame({"value": "x" * MAX_FRAME_BYTES})
    with pytest.raises(ValueError, match="JSON"):
        decode_frame((1).to_bytes(4, "big") + b"!", HoldoutRequest)


def test_frame_decoder_rejects_mismatched_and_noncanonical_json():
    body = json.dumps(request(), separators=(",", ":")).encode()
    frame = len(body).to_bytes(4, "big") + body + b"{}"
    with pytest.raises(ValueError, match="length"):
        decode_frame(frame, HoldoutRequest)


def test_rpc_client_correlates_single_exchange_without_retry():
    response = HoldoutResponse(
        request_id="request-001",
        task_id="task-001",
        context_ref="context-001",
        status="CHECKED_ARCHIVED_OBSERVATION",
        evidence_ref="validation-summary-001",
        summary_sha256="a" * 64,
        aggregate={"outcome": "REJECTED", "gate_count": 5},
    )

    class Transport:
        calls = []

        def exchange(self, frame, *, timeout_ms):
            self.calls.append((frame, timeout_ms))
            return encode_frame(response)

    transport = Transport()
    result = HoldoutRpcClient(transport, timeout_ms=700).call(
        HoldoutRequest.model_validate(request())
    )
    assert result == response
    assert len(transport.calls) == 1
    assert transport.calls[0][1] == 700


def test_rpc_client_timeout_is_not_retried():
    class TimeoutTransport:
        calls = 0

        def exchange(self, frame, *, timeout_ms):
            self.calls += 1
            raise TimeoutError("timed out")

    transport = TimeoutTransport()
    client = HoldoutRpcClient(transport, timeout_ms=250)
    with pytest.raises(TimeoutError):
        client.call(HoldoutRequest.model_validate(request()))
    assert transport.calls == 1


def test_rpc_client_preserves_only_the_bounded_service_failure_code():
    from ai_quant_os.holdout_validator_rpc import HoldoutRpcError

    request_model = HoldoutRequest.model_validate(request())
    failure = HoldoutServiceFailure(
        request_id=request_model.request_id,
        task_id=request_model.task_id,
        context_ref=request_model.context_ref,
        error_code="SERVICE_UNAVAILABLE",
    )

    class FailureTransport:
        def exchange(self, _frame, *, timeout_ms):
            return encode_frame(failure)

    with pytest.raises(HoldoutRpcError) as raised:
        HoldoutRpcClient(FailureTransport()).call(request_model)
    assert raised.value.error_code == "SERVICE_UNAVAILABLE"
    assert str(raised.value) == "HOLDOUT validator returned SERVICE_UNAVAILABLE"


def test_service_rpc_handler_returns_bound_result_and_drops_malformed_frames():
    request_model = HoldoutRequest.model_validate(request())
    response = HoldoutResponse(
        request_id=request_model.request_id,
        task_id=request_model.task_id,
        context_ref=request_model.context_ref,
        status="CHECKED_ARCHIVED_OBSERVATION",
        evidence_ref="validation-summary-001",
        summary_sha256="b" * 64,
        aggregate={"outcome": "REJECTED", "gate_count": 5},
    )

    reply = process_rpc_frame(
        encode_frame(request_model),
        lambda received: assert_request_identity(received, request_model) or response,
    )

    assert decode_frame(reply, HoldoutResponse) == response
    assert process_rpc_frame(b"bad", lambda _: response) is None


def assert_request_identity(actual, expected):
    assert actual == expected
    return False


def test_service_rpc_handler_sanitizes_internal_exceptions():
    request_model = HoldoutRequest.model_validate(request())
    reply = process_rpc_frame(
        encode_frame(request_model),
        lambda _: (_ for _ in ()).throw(RuntimeError("private CSV row must not leak")),
    )
    failure = decode_frame(reply, HoldoutServiceFailure)
    assert failure.error_code == "SERVICE_UNAVAILABLE"
    assert "private CSV" not in failure.model_dump_json()


@pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe identity test")
def test_named_pipe_client_rejects_same_user_server_without_service_sid():
    import pywintypes
    import win32file
    import win32pipe
    import winerror

    pipe_name = rf"\\.\pipe\AIQuantHoldout.TestSpoof.{uuid.uuid4().hex}"
    server_done = threading.Event()
    server_errors = []

    def fake_server():
        handle = win32pipe.CreateNamedPipe(
            pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1,
            4096,
            4096,
            2000,
            None,
        )
        try:
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as error:
                if error.winerror != winerror.ERROR_PIPE_CONNECTED:
                    raise
            try:
                win32file.ReadFile(handle, 1)
                server_errors.append(AssertionError("Client sent before verifying server identity"))
            except pywintypes.error:
                pass
        except Exception as error:
            server_errors.append(error)
        finally:
            win32file.CloseHandle(handle)
            server_done.set()

    thread = threading.Thread(target=fake_server, daemon=True)
    thread.start()
    rpc_request = HoldoutRequest.model_validate(request())
    client = HoldoutRpcClient(
        WindowsNamedPipeTransport("S-1-5-80-1-2-3-4-5", pipe_name=pipe_name),
        timeout_ms=2000,
    )
    with pytest.raises(PermissionError, match="validator service SID"):
        client.call(rpc_request)
    assert server_done.wait(3)
    assert not server_errors

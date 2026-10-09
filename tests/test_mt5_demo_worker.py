import importlib.util
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from test_mt5_demo_observation import NOW, NativeFixture, request
from ai_quant_os.mt5_demo_observation import capture_demo


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_demo_worker") is not None
    from ai_quant_os import mt5_demo_worker

    return mt5_demo_worker


def test_disabled_supervision_never_launches_process(monkeypatch):
    module = implementation()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("No worker expected"))
    result = module.supervise_demo(request(False), clock=lambda: NOW)
    assert result.state == "DISABLED" and result.report.state == "DISABLED"
    assert result.worker_custody == "NOT_STARTED"


def test_worker_invalid_input_is_sanitized_before_native_import():
    module = implementation()
    process = subprocess.run(
        [sys.executable, "-I", str(Path(module.__file__).resolve())],
        input=b'{"password":"private-secret","operation":"ORDER_SEND"}',
        capture_output=True,
        timeout=10,
    )
    result = json.loads(process.stdout)
    assert result["code"] == "INVALID_REQUEST"
    assert b"private-secret" not in process.stdout + process.stderr
    assert process.returncode == 0


def test_worker_disabled_protocol_never_loads_native_sdk():
    module = implementation()
    process = subprocess.run(
        [sys.executable, "-I", str(Path(module.__file__).resolve())],
        input=module.encode_request(request(False)),
        capture_output=True,
        timeout=10,
    )
    result = json.loads(process.stdout)
    assert result["report"]["state"] == "DISABLED"
    assert process.stderr == b""


def test_public_capture_is_bound_to_enrollment_mapping_and_expiry():
    module = implementation()
    original = request()
    report = capture_demo(original, NativeFixture(), clock=lambda: NOW)
    assert module.validate_capture(report.model_dump_json().encode(), original, NOW) == report
    with pytest.raises(ValueError):
        module.validate_capture(
            report.model_dump_json().encode(), original, NOW + timedelta(seconds=3)
        )
    altered = json.loads(report.model_dump_json())
    altered["snapshot"]["quotes"][0]["venue_symbol"] = "Other"
    with pytest.raises(ValueError):
        module.validate_capture(json.dumps(altered).encode(), original, NOW)


def test_invalid_capture_state_or_authority_is_rejected():
    module = implementation()
    original = request()
    report = capture_demo(original, NativeFixture(), clock=lambda: NOW)
    altered = json.loads(report.model_dump_json())
    altered["state"] = "WITHHELD"
    with pytest.raises(ValueError):
        module.validate_capture(json.dumps(altered).encode(), original, NOW)
    altered["may_submit_orders"] = True
    with pytest.raises(ValueError):
        module.validate_capture(json.dumps(altered).encode(), original, NOW)


def launch_fixture(monkeypatch, module, script):
    original = subprocess.Popen
    calls = []

    def launch(args, **kwargs):
        assert args == [sys.executable, "-I", str(Path(module.__file__).resolve())]
        assert all("fixture-demo-read" not in arg for arg in args)
        calls.append(args)
        return original([sys.executable, "-I", "-c", script], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    return calls


def test_parent_terminates_only_its_timed_out_child_without_retry(monkeypatch):
    module = implementation()
    monkeypatch.setattr(module, "WORKER_SECONDS", 0.25)
    calls = launch_fixture(monkeypatch, module, "import time; time.sleep(20)")
    result = module.supervise_demo(request(), clock=lambda: NOW)
    assert result.code == "TIMEOUT" and result.report is None
    assert result.automatic_retry_allowed is False and result.terminal_custody == "UNVERIFIED"
    assert len(calls) == 1


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_parent_discards_excess_output_and_does_not_return_private_diagnostics(monkeypatch, stream):
    module = implementation()
    calls = launch_fixture(
        monkeypatch, module, f"import sys; sys.{stream}.write('private-secret' * 20000)"
    )
    result = module.supervise_demo(request(), clock=lambda: NOW)
    assert result.code == "OUTPUT_LIMIT" and result.report is None
    assert "private-secret" not in result.model_dump_json() and len(calls) == 1


def test_parent_validates_real_pipe_reply_with_bound_fixture_capture(monkeypatch):
    module = implementation()
    report = capture_demo(request(), NativeFixture(), clock=lambda: NOW)
    reply = json.dumps({"code": "OK", "report": json.loads(report.model_dump_json())})
    launch_fixture(
        monkeypatch, module, f"import sys; sys.stdin.buffer.read(); sys.stdout.write({reply!r})"
    )
    result = module.supervise_demo(request(), clock=lambda: NOW)
    assert result.state == "RETURNED" and result.report == report
    assert result.worker_custody == "EXIT_CONFIRMED"


@pytest.mark.parametrize(
    "reply",
    [
        '{"code":"OK","report":null}',
        '{"code":"OK","code":"SDK_UNAVAILABLE"}',
        "private-secret invalid json",
    ],
)
def test_parent_rejects_malformed_and_duplicate_pipe_replies(monkeypatch, reply):
    module = implementation()
    launch_fixture(
        monkeypatch, module, f"import sys; sys.stdin.buffer.read(); sys.stdout.write({reply!r})"
    )
    result = module.supervise_demo(request(), clock=lambda: NOW)
    assert result.code == "PROTOCOL" and result.report is None
    assert "private-secret" not in result.model_dump_json()


def test_enabled_worker_missing_terminal_does_not_connect():
    module = implementation()
    result = module.supervise_demo(request(), clock=lambda: NOW)
    assert result.state == "WITHHELD" and result.worker_reply_code == "TERMINAL_UNAVAILABLE"
    assert result.worker_custody == "EXIT_CONFIRMED"


def test_worker_refuses_oversize_input():
    module = implementation()
    assert module.worker_request(b" " * (module.REQUEST_LIMIT + 1)).code == "INVALID_REQUEST"


def test_unknown_owned_child_is_retained_and_blocks_another_launch(monkeypatch):
    from io import BytesIO

    module = implementation()
    monkeypatch.setattr(module, "WORKER_SECONDS", 0.01)

    class UncertainChild:
        def __init__(self):
            self.stdin, self.stdout, self.stderr = BytesIO(), BytesIO(), BytesIO()
            self.returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            raise OSError("fixture cleanup uncertainty")

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("fixture", timeout)

    child = UncertainChild()
    launches = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launches.append(child) or child)
    result = module.supervise_demo(request(), clock=lambda: NOW)
    assert result.worker_custody == "UNKNOWN" and result.worker_id is not None
    next_result = module.supervise_demo(request(), clock=lambda: NOW)
    assert next_result.code == "CUSTODY_UNCERTAIN" and next_result.worker_id == result.worker_id
    assert launches == [child]
    retained = module.inspect_uncertain_worker()
    assert retained.worker_id == result.worker_id and retained.worker_custody == "UNKNOWN"
    child.returncode = 0
    released = module.inspect_uncertain_worker()
    assert released.worker_id == result.worker_id and released.worker_custody == "EXIT_CONFIRMED"


def test_busy_supervisor_does_not_launch_another_child(monkeypatch):
    module = implementation()
    assert module._supervision_lock.acquire(blocking=False)
    try:
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Busy supervisor"))
        result = module.supervise_demo(request(), clock=lambda: NOW)
        assert result.code == "BUSY" and result.worker_custody == "NOT_STARTED"
    finally:
        module._supervision_lock.release()


def test_pipe_start_failure_stops_owned_child_and_sanitizes_exception(monkeypatch):
    module = implementation()
    launch_fixture(monkeypatch, module, "import time; time.sleep(20)")

    def fail(self):
        raise RuntimeError("private-secret")

    monkeypatch.setattr(module.BoundedPipes, "start", fail)
    result = module.supervise_demo(request(), clock=lambda: NOW)
    assert result.code == "WORKER_EXIT" and result.worker_custody == "EXIT_CONFIRMED"
    assert "private-secret" not in result.model_dump_json()


def test_oversize_request_is_withheld_before_launch(monkeypatch):
    from pydantic import SecretStr

    module = implementation()
    original = request()
    fields = {name: getattr(original, name) for name in type(original).model_fields}
    fields["password"] = SecretStr("fixture" * 3000)
    large = type(original).model_validate(fields)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Oversize request"))
    result = module.supervise_demo(large, clock=lambda: NOW)
    assert result.code == "INVALID_REQUEST" and result.worker_custody == "NOT_STARTED"


def test_service_password_environment_is_not_inherited_by_worker(monkeypatch):
    module = implementation()
    monkeypatch.setenv("AI_QUANT_MT5_PASSWORD", "fixture-service-password")
    observed = []

    def launch(*args, **kwargs):
        observed.append(kwargs.get("env"))
        raise OSError("fixture unavailable worker")

    monkeypatch.setattr(subprocess, "Popen", launch)
    module.supervise_demo(request())
    assert observed and observed[0] is not None
    assert "AI_QUANT_MT5_PASSWORD" not in observed[0]

import importlib.util
import sqlite3
from datetime import timedelta
from uuid import uuid4

import pytest

from ai_quant_os.mt5_demo_observation import capture_demo
from ai_quant_os.mt5_demo_worker import SupervisedCapture
from test_mt5_demo_observation import NOW, NativeFixture, request


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_demo_gateway") is not None
    from ai_quant_os import mt5_demo_gateway

    return mt5_demo_gateway


def successful_runner(calls):
    def run(enrollment, *, clock, worker_id):
        calls.append(worker_id)
        return SupervisedCapture(
            state="RETURNED",
            worker_custody="EXIT_CONFIRMED",
            worker_id=worker_id,
            worker_reply_code="OK",
            report=capture_demo(enrollment, NativeFixture(), clock=clock),
        )

    return run


def test_restart_replays_fresh_receipt_without_another_capture(tmp_path):
    module = implementation()
    calls = []
    path = tmp_path / "gateway.sqlite"
    attempt = uuid4()
    first = module.DemoObservationGateway(path, runner=successful_runner(calls), clock=lambda: NOW)
    captured = first.capture(request(), attempt)
    assert captured.state == "RETURNED" and captured.report.snapshot is not None
    first.close()
    restarted = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    assert restarted.capture(request(), attempt) == captured
    assert len(calls) == 1


def test_restart_preserves_unfinished_attempt_and_blocks_new_dispatch(tmp_path):
    module = implementation()
    path = tmp_path / "gateway.sqlite"
    calls = []

    def interrupted(enrollment, *, clock, worker_id):
        calls.append(worker_id)
        raise KeyboardInterrupt()

    gateway = module.DemoObservationGateway(path, runner=interrupted, clock=lambda: NOW)
    attempt = uuid4()
    with pytest.raises(KeyboardInterrupt):
        gateway.capture(request(), attempt)
    gateway.close()
    restarted = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    assert restarted.capture(request(), attempt).code == "CUSTODY_UNCERTAIN"
    assert restarted.capture(request(), uuid4()).code == "CUSTODY_UNCERTAIN"
    assert len(calls) == 1


def test_expired_receipt_is_withheld_without_refreshing_attempt(tmp_path):
    module = implementation()
    path = tmp_path / "gateway.sqlite"
    calls = []
    attempt = uuid4()
    gateway = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    gateway.capture(request(), attempt)
    gateway.close()
    restarted = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW + timedelta(seconds=3)
    )
    result = restarted.capture(request(), attempt)
    assert result.code == "STALE_RESULT" and result.report is None and len(calls) == 1


def test_conflicting_attempt_identity_is_rejected_before_dispatch(tmp_path):
    module = implementation()
    calls = []
    gateway = module.DemoObservationGateway(
        tmp_path / "gateway.sqlite", runner=successful_runner(calls), clock=lambda: NOW
    )
    attempt = uuid4()
    gateway.capture(request(), attempt)
    original = request()
    fields = {name: getattr(original, name) for name in type(original).model_fields}
    fields["expected_login"] = 610002
    result = gateway.capture(type(original).model_validate(fields), attempt)
    assert result.code == "ATTEMPT_CONFLICT" and len(calls) == 1


def test_database_contains_no_connection_secrets(tmp_path):
    module = implementation()
    path = tmp_path / "gateway.sqlite"
    gateway = module.DemoObservationGateway(path, runner=successful_runner([]), clock=lambda: NOW)
    gateway.capture(request(), uuid4())
    gateway.close()
    connection = sqlite3.connect(path)
    raw = "\n".join(connection.iterdump())
    connection.close()
    for private in [
        "fixture-demo-read",
        "fixture-demo-server",
        "610001",
        "terminal64.exe",
        "private-name",
    ]:
        assert private not in raw


def test_corrupt_persisted_state_withholds_all_dispatch(tmp_path):
    module = implementation()
    path = tmp_path / "gateway.sqlite"
    calls = []
    gateway = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    attempt = uuid4()
    gateway.capture(request(), attempt)
    gateway.close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE demo_gateway_attempts SET state='RESOLVED'")
    connection.commit()
    connection.close()
    restarted = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    assert restarted.capture(request(), uuid4()).code == "STORAGE_UNAVAILABLE"
    assert len(calls) == 1


def test_uncertain_custody_requires_exact_owned_handle_before_next_attempt(tmp_path, monkeypatch):
    module = implementation()
    calls = []

    def uncertain(enrollment, *, clock, worker_id):
        calls.append(worker_id)
        return SupervisedCapture(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway = module.DemoObservationGateway(
        tmp_path / "gateway.sqlite", runner=uncertain, clock=lambda: NOW
    )
    attempt = uuid4()
    assert gateway.capture(request(), attempt).code == "CUSTODY_UNCERTAIN"
    monkeypatch.setattr(
        module,
        "inspect_uncertain_worker",
        lambda: SupervisedCapture(
            state="WITHHELD", worker_id=uuid4(), worker_custody="EXIT_CONFIRMED"
        ),
    )
    assert gateway.reconcile(request(), attempt).code == "CUSTODY_UNCERTAIN"
    monkeypatch.setattr(
        module,
        "inspect_uncertain_worker",
        lambda: SupervisedCapture(
            state="WITHHELD", worker_id=calls[0], worker_custody="EXIT_CONFIRMED"
        ),
    )
    recovered = gateway.reconcile(request(), attempt)
    assert recovered.result.worker_custody == "EXIT_CONFIRMED" and recovered.report is None
    assert gateway.capture(request(), attempt) == recovered
    gateway.runner = successful_runner(calls)
    assert gateway.capture(request(), uuid4()).state == "RETURNED" and len(calls) == 2


def test_new_gateway_owner_cannot_resolve_prior_owners_uncertainty(tmp_path, monkeypatch):
    module = implementation()
    path = tmp_path / "gateway.sqlite"
    worker_ids = []

    def uncertain(enrollment, *, clock, worker_id):
        worker_ids.append(worker_id)
        return SupervisedCapture(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway = module.DemoObservationGateway(path, runner=uncertain, clock=lambda: NOW)
    attempt = uuid4()
    gateway.capture(request(), attempt)
    gateway.close()
    restarted = module.DemoObservationGateway(path, runner=successful_runner([]), clock=lambda: NOW)
    monkeypatch.setattr(
        module, "inspect_uncertain_worker", lambda: pytest.fail("Foreign owner cannot poll")
    )
    assert restarted.reconcile(request(), attempt).code == "CUSTODY_UNCERTAIN"


def test_returned_report_requires_confirmed_worker_and_valid_response_shape(tmp_path):
    module = implementation()

    def invalid(enrollment, *, clock, worker_id):
        return SupervisedCapture(
            state="RETURNED",
            worker_id=worker_id,
            worker_custody="NOT_STARTED",
            report=capture_demo(enrollment, NativeFixture(), clock=clock),
        )

    gateway = module.DemoObservationGateway(
        tmp_path / "gateway.sqlite", runner=invalid, clock=lambda: NOW
    )
    assert gateway.capture(request(), uuid4()).code == "INVALID_RESULT"
    assert gateway.capture(request(), uuid4()).code == "CUSTODY_UNCERTAIN"


def test_two_connections_cannot_dispatch_overlapping_attempts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    module = implementation()
    entered, release = Event(), Event()
    calls = []

    def held(enrollment, *, clock, worker_id):
        entered.set()
        assert release.wait(5)
        return successful_runner(calls)(enrollment, clock=clock, worker_id=worker_id)

    path = tmp_path / "gateway.sqlite"
    first = module.DemoObservationGateway(path, runner=held, clock=lambda: NOW)
    second = module.DemoObservationGateway(path, runner=successful_runner(calls), clock=lambda: NOW)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first.capture, request(), uuid4())
        try:
            assert entered.wait(3)
            assert second.capture(request(), uuid4()).code == "CUSTODY_UNCERTAIN"
        finally:
            release.set()
        assert future.result(timeout=5).state == "RETURNED"
    assert len(calls) == 1


def test_receipt_replays_in_an_independent_python_process(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    from ai_quant_os.mt5_demo_worker import encode_request

    module = implementation()
    path = tmp_path / "gateway.sqlite"
    attempt = uuid4()
    gateway = module.DemoObservationGateway(path, runner=successful_runner([]), clock=lambda: NOW)
    original = gateway.capture(request(), attempt)
    gateway.close()
    source = str(Path(module.__file__).resolve().parent.parent)
    script = f"""
import sys, json
sys.path.insert(0, {source!r})
from pathlib import Path
from uuid import UUID
from datetime import datetime
from ai_quant_os.mt5_demo_observation import DemoCaptureRequest
from ai_quant_os.mt5_demo_gateway import DemoObservationGateway
def denied(*args, **kwargs):
    raise AssertionError('Restart replay must not dispatch')
enrollment = DemoCaptureRequest.model_validate(json.loads(sys.stdin.buffer.read())['request'])
gateway = DemoObservationGateway(Path(sys.argv[1]), runner=denied, clock=lambda: datetime.fromisoformat({NOW.isoformat()!r}))
print(gateway.capture(enrollment, UUID(sys.argv[2])).model_dump_json())
gateway.close()
"""
    process = subprocess.run(
        [sys.executable, "-I", "-c", script, str(path), str(attempt)],
        input=encode_request(request()),
        capture_output=True,
        timeout=20,
    )
    assert process.returncode == 0 and process.stderr == b""
    assert module.GatewayReply.model_validate_json(process.stdout) == original


def test_atomic_receipt_failure_preserves_blocking_reservation(tmp_path, monkeypatch):
    module = implementation()
    path = tmp_path / "gateway.sqlite"
    calls = []
    gateway = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    append = module.AuditChain.append

    def fail(chain, key, event, **kwargs):
        if event["kind"] == "FINISHED":
            raise sqlite3.OperationalError("fixture write failure")
        return append(chain, key, event, **kwargs)

    monkeypatch.setattr(module.AuditChain, "append", fail)
    assert gateway.capture(request(), uuid4()).code == "STORAGE_UNAVAILABLE"
    gateway.close()
    restarted = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    assert restarted.capture(request(), uuid4()).code == "CUSTODY_UNCERTAIN" and len(calls) == 1


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM audit_events",
        "DELETE FROM demo_gateway_attempts",
        "DELETE FROM audit_head",
    ],
)
def test_missing_history_or_projection_is_not_silently_recreated(tmp_path, sql):
    module = implementation()
    path = tmp_path / "gateway.sqlite"
    calls = []
    gateway = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    gateway.capture(request(), uuid4())
    gateway.close()
    with sqlite3.connect(path) as connection:
        connection.execute(sql)
    restarted = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    assert restarted.capture(request(), uuid4()).code == "STORAGE_UNAVAILABLE" and len(calls) == 1


def test_existing_empty_database_is_not_adopted_as_a_new_journal(tmp_path):
    module = implementation()
    path = tmp_path / "gateway.sqlite"
    path.touch()
    calls = []
    gateway = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    assert gateway.capture(request(), uuid4()).code == "STORAGE_UNAVAILABLE" and calls == []


def test_disabled_gateway_does_not_dispatch_or_change_journal(tmp_path):
    module = implementation()
    path = tmp_path / "gateway.sqlite"
    calls = []
    gateway = module.DemoObservationGateway(
        path, runner=successful_runner(calls), clock=lambda: NOW
    )
    assert gateway.capture(request(False), uuid4()).state == "DISABLED" and calls == []
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM demo_gateway_attempts").fetchone()[0] == 0


def test_capacity_exhaustion_never_purges_old_receipts(tmp_path, monkeypatch):
    module = implementation()
    monkeypatch.setattr(module, "MAX_ATTEMPTS", 2)
    calls = []
    gateway = module.DemoObservationGateway(
        tmp_path / "gateway.sqlite", runner=successful_runner(calls), clock=lambda: NOW
    )
    first = uuid4()
    saved = gateway.capture(request(), first)
    gateway.capture(request(), uuid4())
    assert gateway.capture(request(), uuid4()).code == "CAPACITY"
    assert gateway.capture(request(), first) == saved and len(calls) == 2


def test_gateway_default_supervisor_uses_reserved_worker_identity_without_sdk(tmp_path):
    module = implementation()
    gateway = module.DemoObservationGateway(tmp_path / "gateway.sqlite", clock=lambda: NOW)
    attempt = uuid4()
    returned = gateway.capture(request(), attempt)
    assert returned.result.worker_reply_code == "TERMINAL_UNAVAILABLE"
    assert (
        returned.result.worker_custody == "EXIT_CONFIRMED" and returned.result.worker_id is not None
    )
    assert gateway.capture(request(), attempt) == returned


def test_confirmed_exit_proof_survives_failed_reconciliation_write(tmp_path, monkeypatch):
    module = implementation()
    ids = []

    def uncertain(enrollment, *, clock, worker_id):
        ids.append(worker_id)
        return SupervisedCapture(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway = module.DemoObservationGateway(
        tmp_path / "gateway.sqlite", runner=uncertain, clock=lambda: NOW
    )
    attempt = uuid4()
    gateway.capture(request(), attempt)
    observations = iter(
        [
            SupervisedCapture(state="WITHHELD", worker_id=ids[0], worker_custody="EXIT_CONFIRMED"),
            SupervisedCapture(state="WITHHELD", worker_custody="NOT_STARTED"),
        ]
    )
    monkeypatch.setattr(module, "inspect_uncertain_worker", lambda: next(observations))
    append = module.AuditChain.append

    def fail(chain, key, event, **kwargs):
        if event["kind"] == "RECONCILED":
            raise sqlite3.OperationalError("fixture reconciliation write failure")
        return append(chain, key, event, **kwargs)

    monkeypatch.setattr(module.AuditChain, "append", fail)
    assert gateway.reconcile(request(), attempt).code == "STORAGE_UNAVAILABLE"
    monkeypatch.setattr(module.AuditChain, "append", append)
    assert gateway.reconcile(request(), attempt).result.worker_custody == "EXIT_CONFIRMED"


def test_actual_supervisor_custody_is_bound_to_durable_attempt(tmp_path, monkeypatch):
    from io import BytesIO
    import subprocess

    from ai_quant_os import mt5_demo_worker as worker

    module = implementation()

    class UncertainChild:
        def __init__(self):
            self.stdin, self.stdout, self.stderr = BytesIO(), BytesIO(), BytesIO()
            self.returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            raise OSError("fixture uncertainty")

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("fixture", timeout)

    child = UncertainChild()
    launches = []
    monkeypatch.setattr(worker, "WORKER_SECONDS", 0.01)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launches.append(child) or child)
    gateway = module.DemoObservationGateway(tmp_path / "gateway.sqlite", clock=lambda: NOW)
    attempt = uuid4()
    try:
        assert gateway.capture(request(), attempt).code == "CUSTODY_UNCERTAIN"
        assert (
            gateway.capture(request(), uuid4()).code == "CUSTODY_UNCERTAIN" and len(launches) == 1
        )
        child.returncode = 0
        released = gateway.reconcile(request(), attempt)
        assert released.result.worker_custody == "EXIT_CONFIRMED" and released.report is None
        assert gateway.capture(request(), attempt) == released
    finally:
        child.returncode = 0
        worker.inspect_uncertain_worker()


def test_exported_receipt_contract_accepts_runtime_result_and_denies_order_authority(tmp_path):
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator, ValidationError

    module = implementation()
    schema_path = (
        Path(__file__).resolve().parents[1] / "docs/schemas/demo-gateway-receipt.schema.json"
    )
    assert schema_path.is_file()
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    gateway = module.DemoObservationGateway(
        tmp_path / "gateway.sqlite", runner=successful_runner([]), clock=lambda: NOW
    )
    receipt = json.loads(gateway.capture(request(), uuid4()).model_dump_json())
    assert receipt["schema_version"] == "1.0"
    validator.validate(receipt)
    receipt["may_submit_orders"] = True
    with pytest.raises(ValidationError):
        validator.validate(receipt)


def test_live_owner_can_reconcile_confirmed_interrupt_cleanup(tmp_path, monkeypatch):
    from io import BytesIO
    import subprocess

    from ai_quant_os import mt5_demo_worker as worker

    module = implementation()

    class Child:
        def __init__(self):
            self.stdin, self.stdout, self.stderr = BytesIO(), BytesIO(), BytesIO()
            self.returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def wait(self, timeout):
            return self.returncode

    child = Child()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: child)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(worker, "_await_reply", interrupt)
    gateway = module.DemoObservationGateway(tmp_path / "gateway.sqlite", clock=lambda: NOW)
    attempt = uuid4()
    with pytest.raises(KeyboardInterrupt):
        gateway.capture(request(), attempt)
    assert child.poll() == -9
    recovered = gateway.reconcile(request(), attempt)
    assert recovered.result.worker_custody == "EXIT_CONFIRMED" and recovered.report is None

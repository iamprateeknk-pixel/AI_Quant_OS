import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from ai_quant_os import mt5_demo_worker as worker
from ai_quant_os import mt5_demo_gateway as gateway
from test_mt5_account_sync import NOW, SyncNative, implementation, sync_request


def test_disabled_sync_uses_supervisor_without_child(monkeypatch):
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Disabled sync"))
    )
    result = worker.supervise_demo(sync_request(False), clock=lambda: NOW)
    assert result.observation_kind == "ACCOUNT_SYNC" and result.report.state == "DISABLED"


def test_sync_wire_runs_fixed_worker_without_loading_missing_terminal():
    request = sync_request()
    result = worker.supervise_demo(request, clock=lambda: NOW)
    assert result.worker_reply_code == "TERMINAL_UNAVAILABLE" and result.report is None
    assert result.observation_kind == "ACCOUNT_SYNC" and result.worker_custody == "EXIT_CONFIRMED"


def test_sync_normalized_payload_crosses_real_pipe_and_is_validated(monkeypatch):
    request = sync_request()
    report = implementation().capture_sync(request, SyncNative(), clock=lambda: NOW)
    reply = json.dumps(
        {
            "observation_kind": "ACCOUNT_SYNC",
            "code": "OK",
            "report": json.loads(report.model_dump_json()),
        }
    )
    original = subprocess.Popen

    def fixture(args, **kwargs):
        assert args == [sys.executable, "-I", str(Path(worker.__file__).resolve())]
        script = f"import sys; sys.stdin.buffer.read(); sys.stdout.write({reply!r})"
        return original([sys.executable, "-I", "-c", script], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fixture)
    result = worker.supervise_demo(request, clock=lambda: NOW)
    assert result.report == report and result.worker_custody == "EXIT_CONFIRMED"


def test_sync_gateway_uses_journal_and_does_not_dispatch_replay(tmp_path):
    calls = []

    def runner(request, *, clock, worker_id):
        calls.append(worker_id)
        return worker.SupervisedSync(
            state="RETURNED",
            worker_custody="EXIT_CONFIRMED",
            worker_id=worker_id,
            worker_reply_code="OK",
            report=implementation().capture_sync(request, SyncNative(), clock=clock),
        )

    path = tmp_path / "gateway.sqlite"
    instance = gateway.DemoObservationGateway(path, runner=runner, clock=lambda: NOW)
    attempt = uuid4()
    first = instance.capture(sync_request(), attempt)
    assert first.observation_kind == "ACCOUNT_SYNC" and first.report.batch.fills[0].side == "BUY"
    instance.close()
    restarted = gateway.DemoObservationGateway(path, runner=runner, clock=lambda: NOW)
    assert restarted.capture(sync_request(), attempt) == first and len(calls) == 1


def test_sync_parent_rejects_forged_scope_quantity_and_cash_identity():
    import pytest

    request = sync_request()
    report = implementation().capture_sync(request, SyncNative(), clock=lambda: NOW)
    for section, field, value in [
        ("positions", "quantity_units", "9000"),
        ("fills", "event_ref", "0" * 64),
        ("cash_events", "kind", "BUY"),
    ]:
        data = json.loads(report.model_dump_json())
        data["batch"][section][0][field] = value
        with pytest.raises(ValueError):
            worker.validate_capture(json.dumps(data).encode(), request, NOW)


def test_sync_gateway_expiry_and_window_conflict_do_not_refresh(tmp_path):
    from datetime import timedelta

    calls = []

    def runner(request, *, clock, worker_id):
        calls.append(worker_id)
        return worker.SupervisedSync(
            state="RETURNED",
            worker_custody="EXIT_CONFIRMED",
            worker_id=worker_id,
            worker_reply_code="OK",
            report=implementation().capture_sync(request, SyncNative(), clock=clock),
        )

    instance = gateway.DemoObservationGateway(
        tmp_path / "gateway.sqlite", runner=runner, clock=lambda: NOW
    )
    attempt = uuid4()
    instance.capture(sync_request(), attempt)
    changed = implementation().DemoSyncRequest(
        connection=sync_request().connection,
        history_start=NOW - timedelta(hours=2),
        history_end=NOW,
    )
    assert instance.capture(changed, attempt).code == "ATTEMPT_CONFLICT"
    instance.clock = lambda: NOW + timedelta(seconds=3)
    stale = instance.capture(sync_request(), attempt)
    assert stale.code == "STALE_RESULT" and stale.report is None and len(calls) == 1


def test_quote_contract_schema_is_unchanged_and_sync_contract_is_separate():
    old = Path(__file__).resolve().parents[1] / "docs/schemas/demo-gateway-receipt.schema.json"
    assert gateway.GatewayReply.model_json_schema() == json.loads(old.read_text(encoding="utf-8"))


def test_exported_sync_contract_matches_runtime_and_blocks_order_authority(tmp_path):
    import pytest
    from jsonschema import Draft202012Validator, ValidationError

    path = (
        Path(__file__).resolve().parents[1] / "docs/schemas/demo-account-sync-receipt.schema.json"
    )
    assert path.is_file()
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema == gateway.SyncGatewayReply.model_json_schema()
    instance = gateway.DemoObservationGateway(tmp_path / "gateway.sqlite", clock=lambda: NOW)
    reply = json.loads(instance.capture(sync_request(False), uuid4()).model_dump_json())
    validator = Draft202012Validator(schema)
    validator.validate(reply)
    reply["may_submit_orders"] = True
    with pytest.raises(ValidationError):
        validator.validate(reply)


def test_sync_uncertainty_blocks_quote_attempt_and_survives_restart(tmp_path):
    from test_mt5_demo_observation import request

    calls = []

    def uncertain(request, *, clock, worker_id):
        calls.append(worker_id)
        return worker.SupervisedSync(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    path = tmp_path / "gateway.sqlite"
    instance = gateway.DemoObservationGateway(path, runner=uncertain, clock=lambda: NOW)
    attempt = uuid4()
    assert instance.capture(sync_request(), attempt).code == "CUSTODY_UNCERTAIN"
    assert instance.capture(request(), uuid4()).code == "CUSTODY_UNCERTAIN"
    instance.close()
    restarted = gateway.DemoObservationGateway(path, runner=uncertain, clock=lambda: NOW)
    assert restarted.capture(sync_request(), attempt).code == "CUSTODY_UNCERTAIN"
    assert restarted.reconcile(sync_request(), attempt).code == "CUSTODY_UNCERTAIN"
    assert len(calls) == 1

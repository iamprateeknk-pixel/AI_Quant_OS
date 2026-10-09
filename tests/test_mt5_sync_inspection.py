import importlib.util
import json
import sqlite3

from test_mt5_history_backfill import history_gap, request as backfill_request


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_sync_inspection") is not None
    from ai_quant_os import mt5_sync_inspection

    return mt5_sync_inspection


def test_inspection_reads_current_halt_without_dispatch_or_state_change(tmp_path):
    _, controller, _, _, _, _, calls, _, _, halted = history_gap(tmp_path)
    module = implementation()
    result = module.inspect_sync(controller)
    assert result.state == "READABLE" and result.health == halted
    assert result.gateway_custody == "ALL_FINISHED" and result.projection.state == "EMPTY"
    assert result.pending_receipt.state == "NOT_PENDING" and calls == []
    assert controller.tick() == halted
    assert result.may_capture is False and result.may_submit_orders is False
    assert result.native_attestation == "UNVERIFIED"


def test_inspection_sees_post_command_revision_and_keeps_heartbeat(tmp_path):
    module, controller, _, _, _, _, calls, _, _, halted = history_gap(tmp_path)
    completed = controller.backfill_history(backfill_request(module, halted))
    heartbeat = tmp_path / "health.json"
    heartbeat.write_bytes(b"previous complete heartbeat")
    result = implementation().inspect_sync(controller)
    assert result.health == completed.health and result.health.revision > halted.revision
    assert result.projection.state == "MATCHED" and result.projection.receipts == 1
    assert result.projection.known_deals == 2 and len(calls) == 1
    assert heartbeat.read_bytes() == b"previous complete heartbeat"
    raw = result.model_dump_json()
    assert "balance" not in raw and "expected_login" not in raw and "terminal_path" not in raw


def test_inspection_exposes_failed_receipt_code_without_native_payload(tmp_path):
    from test_mt5_window_reduction import capacity_halt

    _, controller, _, _, _, _, calls, _, halted = capacity_halt(tmp_path)
    result = implementation().inspect_sync(controller)
    assert result.health == halted and result.pending_receipt.state == "FINISHED"
    assert result.pending_receipt.report_code == "CAPACITY"
    assert result.pending_receipt.receipt_ref is not None and len(calls) == 1
    assert "fixture-demo-server" not in result.model_dump_json()


def test_corrupt_controller_inspection_withholds_revision_and_never_dispatches(tmp_path):
    _, controller, _, _, _, _, calls, _, _, _ = history_gap(tmp_path)
    with sqlite3.connect(tmp_path / "controller.sqlite") as db:
        db.execute("DELETE FROM audit_events")
    result = implementation().inspect_sync(controller)
    assert result.state == "PARTIAL" and result.health is None
    assert result.controller_state == "UNAVAILABLE" and calls == []


def test_inspection_cli_needs_no_password_or_native_launch_flag(tmp_path, monkeypatch, capsys):
    from ai_quant_os import mt5_sync_service
    from test_mt5_sync_service import config_document

    _, controller, gateway, projection, _, _, calls, policy, _, halted = history_gap(tmp_path)
    controller.close()
    gateway.close()
    projection.close()
    values = config_document()
    values["schedule"] = policy.model_dump(mode="json")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.delenv("AI_QUANT_MT5_PASSWORD", raising=False)
    monkeypatch.setattr(mt5_sync_service.sys, "platform", "linux")

    def forbidden(*args, **kwargs):
        raise AssertionError("Inspection must not capture or run a timer")

    monkeypatch.setattr(mt5_sync_service, "run_service", forbidden)
    monkeypatch.setattr(mt5_sync_service.DemoObservationGateway, "capture", forbidden)
    assert (
        mt5_sync_service.main(
            [
                "--config",
                str(config),
                "--state-dir",
                str(tmp_path),
                "--inspect-state",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "READABLE" and result["health"]["revision"] == halted.revision
    assert result["may_capture"] is False and calls == []
    assert not (tmp_path / "health.json").exists()


def test_uncertain_receipt_is_visible_without_reconciliation(tmp_path):
    from ai_quant_os.mt5_demo_worker import SupervisedSync

    module, controller, gateway, _, _, _, calls, _, _, gap = history_gap(tmp_path)

    def uncertain(request, *, clock, worker_id):
        return SupervisedSync(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway.runner = uncertain
    halted = controller.backfill_history(backfill_request(module, gap)).health
    result = implementation().inspect_sync(controller)
    assert result.health == halted and result.gateway_custody == "CUSTODY_UNCERTAIN"
    assert result.pending_receipt.state == "UNAVAILABLE"
    assert result.pending_receipt.gateway_code == "CUSTODY_UNCERTAIN" and calls == []
    assert controller.tick() == halted


def test_closed_stores_are_unavailable_without_reopening_or_reset(tmp_path):
    _, controller, gateway, projection, _, _, calls, _, _, _ = history_gap(tmp_path)
    controller.close()
    gateway.close()
    projection.close()
    result = implementation().inspect_sync(controller)
    assert result.state == "UNAVAILABLE" and result.health is None
    assert result.projection.state == "UNAVAILABLE" and calls == []


def test_missing_inspection_state_creates_no_directory(tmp_path, capsys):
    from ai_quant_os.mt5_sync_service import main
    from test_mt5_sync_service import config_document

    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_document()), encoding="utf-8")
    state = tmp_path / "missing"
    assert main(["--config", str(config), "--state-dir", str(state), "--inspect-state"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "INSPECTION_STATE_REQUIRED"
    assert not state.exists()


def test_inspection_refuses_mutating_parameters(tmp_path, capsys):
    from ai_quant_os.mt5_sync_service import main

    assert (
        main(
            [
                "--config",
                str(tmp_path / "unused.json"),
                "--state-dir",
                str(tmp_path / "state"),
                "--inspect-state",
                "--expected-revision",
                "1",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "RECOVERY_INVALID"
    assert not (tmp_path / "state").exists()


def test_inspection_does_not_bypass_enabled_capture_password_gate(tmp_path, monkeypatch, capsys):
    from ai_quant_os.mt5_sync_service import main
    from test_mt5_sync_service import config_document

    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_document()), encoding="utf-8")
    monkeypatch.delenv("AI_QUANT_MT5_PASSWORD", raising=False)
    state = tmp_path / "state"
    assert main(["--config", str(config), "--state-dir", str(state), "--enable-demo-service"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "CONFIG_INVALID"
    assert not state.exists()


def test_clock_inspection_labels_future_state_without_rewriting_it(tmp_path):
    from datetime import timedelta

    _, controller, _, _, _, _, calls, _, _, halted = history_gap(tmp_path)
    result = implementation().inspect_sync(
        controller, clock=lambda: halted.observed_at - timedelta(seconds=1)
    )
    assert result.clock_condition == "BEFORE_STATE" and result.health == halted and calls == []


def test_inspection_exported_contract_matches_runtime_and_denies_capture(tmp_path):
    from pathlib import Path
    import pytest
    from pydantic import TypeAdapter
    from jsonschema import Draft202012Validator, ValidationError

    _, controller, _, _, _, _, _, _, _, _ = history_gap(tmp_path)
    module = implementation()
    path = Path(__file__).resolve().parents[1] / "docs/schemas/demo-sync-inspection.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema == TypeAdapter(module.SyncInspection).json_schema()
    record = module.inspect_sync(controller).model_dump(mode="json")
    validator = Draft202012Validator(schema)
    validator.validate(record)
    record["may_capture"] = True
    with pytest.raises(ValidationError):
        validator.validate(record)


def test_real_inspection_cli_reads_existing_disabled_journals_without_heartbeat(tmp_path):
    import os
    import subprocess
    import sys
    from contextlib import ExitStack
    from pathlib import Path
    from ai_quant_os.mt5_demo_gateway import DemoObservationGateway
    from ai_quant_os.mt5_sync_controller import DemoSyncController
    from ai_quant_os.mt5_sync_projection import SyncProjectionStore
    from ai_quant_os.mt5_sync_service import load_config
    from test_mt5_sync_service import config_document

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config_document(False)), encoding="utf-8")
    config = load_config(config_path, {})
    state = tmp_path / "state"
    state.mkdir()
    with ExitStack() as stack:
        gateway = DemoObservationGateway(state / "gateway.sqlite")
        stack.callback(gateway.close)
        projection = SyncProjectionStore(state / "projection.sqlite")
        stack.callback(projection.close)
        controller = DemoSyncController(
            state / "controller.sqlite",
            config.connection,
            config.schedule,
            gateway,
            projection,
        )
        stack.callback(controller.close)
        revision = controller.inspect_health().revision
    environment = dict(os.environ)
    environment.pop("AI_QUANT_MT5_PASSWORD", None)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.mt5_sync_service",
            "--config",
            str(config_path),
            "--state-dir",
            str(state),
            "--inspect-state",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0 and result.stderr == ""
    record = json.loads(result.stdout)
    assert record["state"] == "READABLE" and record["health"]["revision"] == revision
    assert record["configured_capture_enabled"] is False and record["may_capture"] is False
    assert not (state / "health.json").exists()

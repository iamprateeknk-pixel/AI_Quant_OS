from datetime import timedelta

import pytest

from test_mt5_sync_controller import fresh, setup


def clock_halt(tmp_path):
    module, controller, gateway, projection, native, time, calls, policy = setup(tmp_path)
    healthy = controller.tick()
    time[0] -= timedelta(seconds=1)
    halted = controller.tick()
    assert halted.code == "CLOCK_REGRESSION" and halted.pending_attempt_id is None
    return module, controller, gateway, projection, native, time, calls, policy, healthy, halted


def request(module, halted):
    return module.SyncClockRecoveryRequest(expected_revision=halted.revision)


def test_clock_recovery_preserves_history_and_due_time_without_capture(tmp_path):
    module, controller, _, _, native, time, calls, _, healthy, halted = clock_halt(tmp_path)
    time[0] = healthy.observed_at
    result = controller.recover_clock(request(module, halted))
    assert result.state == "RESUMED" and result.health.state == "STARTING"
    assert result.health.revision == halted.revision + 1
    assert result.health.last_history_end == healthy.last_history_end
    assert result.health.last_success_at == healthy.last_success_at
    assert result.health.next_due_at == healthy.next_due_at
    assert result.health.pending_attempt_id is None and len(calls) == 1
    assert controller.tick() == result.health and len(calls) == 1
    time[0] = result.health.next_due_at
    fresh(native, time)
    assert controller.tick().state == "HEALTHY" and len(calls) == 2


def test_clock_must_catch_up_before_recovery(tmp_path):
    module, controller, _, _, _, _, calls, _, _, halted = clock_halt(tmp_path)
    result = controller.recover_clock(request(module, halted))
    assert result.code == "CLOCK_REGRESSION" and result.health == halted
    assert controller.tick() == halted and len(calls) == 1


def test_clock_recovery_cannot_skip_history_gap(tmp_path):
    module, controller, _, _, _, time, calls, _, _, halted = clock_halt(tmp_path)
    time[0] += timedelta(days=2)
    result = controller.recover_clock(request(module, halted))
    assert result.code == "HISTORY_GAP" and result.health == halted and len(calls) == 1
    assert controller.tick() == halted


def test_resume_is_durable_and_repeat_conflicts(tmp_path):
    module, controller, gateway, projection, _, time, calls, policy, healthy, halted = clock_halt(
        tmp_path
    )
    time[0] = healthy.observed_at
    recovery = request(module, halted)
    result = controller.recover_clock(recovery)
    assert controller.recover_clock(recovery).code == "STATE_CONFLICT"
    controller.close()
    reopened = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        controller.connection,
        policy,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    assert reopened.tick() == result.health and len(calls) == 1


def test_clock_recovery_rejects_pending_capture_halt(tmp_path, monkeypatch):
    from test_mt5_sync_recovery import halted_projection

    module, controller, _, _, _, _, calls, _, halted = halted_projection(tmp_path, monkeypatch)
    result = controller.recover_clock(request(module, halted))
    assert result.code == "STATE_CONFLICT" and result.health == halted and len(calls) == 1


def test_unfinished_gateway_attempt_blocks_clock_resume(tmp_path):
    from uuid import uuid4
    from ai_quant_os.mt5_demo_worker import SupervisedSync
    from test_mt5_account_sync import sync_request

    module, controller, gateway, _, _, time, calls, _, healthy, halted = clock_halt(tmp_path)
    time[0] = healthy.observed_at

    def uncertain(request, *, clock, worker_id):
        return SupervisedSync(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway.runner = uncertain
    gateway.capture(sync_request(), uuid4())
    result = controller.recover_clock(request(module, halted))
    assert result.code == "CUSTODY_UNCERTAIN" and result.health == halted and len(calls) == 1
    assert controller.tick() == halted


def test_corrupt_projection_blocks_clock_resume(tmp_path):
    import sqlite3

    module, controller, _, _, _, time, calls, _, healthy, halted = clock_halt(tmp_path)
    time[0] = healthy.observed_at
    with sqlite3.connect(tmp_path / "projection.sqlite") as db:
        db.execute("DELETE FROM sync_projection_receipts")
    result = controller.recover_clock(request(module, halted))
    assert result.code == "PROJECTION_WITHHELD" and result.health == halted and len(calls) == 1


def test_stale_revision_blocks_clock_resume(tmp_path):
    module, controller, _, _, _, time, calls, _, healthy, halted = clock_halt(tmp_path)
    time[0] = healthy.observed_at
    result = controller.recover_clock(
        module.SyncClockRecoveryRequest(expected_revision=halted.revision + 1)
    )
    assert result.code == "STATE_CONFLICT" and result.health == halted and len(calls) == 1


def test_clock_cli_resumes_once_without_timer_or_capture(tmp_path, monkeypatch, capsys):
    import json
    from ai_quant_os import mt5_sync_service
    from ai_quant_os.mt5_demo_gateway import DemoObservationGateway
    from test_mt5_sync_service import config_document

    module, controller, gateway, projection, _, _, calls, policy, healthy, halted = clock_halt(
        tmp_path
    )
    controller.close()
    gateway.close()
    projection.close()
    values = config_document()
    values["schedule"] = policy.model_dump(mode="json")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.setenv("AI_QUANT_MT5_PASSWORD", "fixture-private-password")
    monkeypatch.setattr(mt5_sync_service.sys, "platform", "win32")
    original = module.DemoSyncController

    def controlled_clock(*args, **kwargs):
        return original(*args, **kwargs, clock=lambda: healthy.observed_at)

    def forbidden(*args, **kwargs):
        raise AssertionError("Clock recovery must not dispatch or start a timer")

    monkeypatch.setattr(mt5_sync_service, "DemoSyncController", controlled_clock)
    monkeypatch.setattr(mt5_sync_service, "run_service", forbidden)
    monkeypatch.setattr(DemoObservationGateway, "capture", forbidden)
    assert (
        mt5_sync_service.main(
            [
                "--config",
                str(config),
                "--state-dir",
                str(tmp_path),
                "--enable-demo-service",
                "--resume-clock-halt",
                "--expected-revision",
                str(halted.revision),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "RESUMED" and result["health"]["state"] == "STARTING"
    assert (
        result["health"]["last_history_end"] == healthy.model_dump(mode="json")["last_history_end"]
    )
    assert result["may_submit_orders"] is False and len(calls) == 1
    assert not (tmp_path / "health.json").exists()


def test_clock_wire_schema_matches_runtime_and_denies_orders():
    import json
    from pathlib import Path
    from pydantic import TypeAdapter
    from jsonschema import Draft202012Validator, ValidationError
    from ai_quant_os.mt5_sync_controller import SyncClockRecoveryRequest, SyncClockRecoveryResult

    path = Path(__file__).resolve().parents[1] / "docs/schemas/demo-sync-clock-recovery.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema == TypeAdapter(SyncClockRecoveryRequest | SyncClockRecoveryResult).json_schema()
    result = SyncClockRecoveryResult(
        state="WITHHELD",
        code="BUSY",
        request=SyncClockRecoveryRequest(expected_revision=2),
    ).model_dump(mode="json")
    validator = Draft202012Validator(schema)
    validator.validate(result)
    result["may_submit_orders"] = True
    with pytest.raises(ValidationError):
        validator.validate(result)


def test_initial_clock_halt_can_resume_without_inventing_observations(tmp_path):
    module, controller, _, projection, _, time, calls, _ = setup(tmp_path)
    original_time = time[0]
    time[0] -= timedelta(seconds=1)
    halted = controller.tick()
    time[0] = original_time
    result = controller.recover_clock(request(module, halted))
    assert result.state == "RESUMED" and result.health.last_success_at is None
    assert result.health.last_history_end is None and projection.view(controller.connection) is None
    assert result.health.next_due_at == original_time and calls == []


def test_backoff_and_failure_count_survive_clock_recovery(tmp_path):
    from types import SimpleNamespace

    module, controller, _, projection, native, time, calls, _ = setup(tmp_path)
    native.terminal_info = lambda: SimpleNamespace(connected=False)
    degraded = controller.tick()
    time[0] -= timedelta(seconds=1)
    halted = controller.tick()
    time[0] = degraded.observed_at
    result = controller.recover_clock(request(module, halted))
    assert result.state == "RESUMED" and result.health.consecutive_failures == 1
    assert result.health.next_due_at == degraded.next_due_at
    assert result.health.last_history_end is None and projection.view(controller.connection) is None
    assert controller.tick() == result.health and len(calls) == 1


def test_corrupt_gateway_audit_withholds_clock_recovery(tmp_path):
    import sqlite3

    module, controller, gateway, _, _, time, calls, _, healthy, halted = clock_halt(tmp_path)
    time[0] = healthy.observed_at
    with sqlite3.connect(tmp_path / "gateway.sqlite") as db:
        db.execute("DELETE FROM audit_events")
    assert gateway.custody_status() == "STORAGE_UNAVAILABLE"
    result = controller.recover_clock(request(module, halted))
    assert result.code == "STORAGE_UNAVAILABLE" and result.health == halted and len(calls) == 1


def test_capacity_withholds_clock_resume_before_shared_checks(tmp_path, monkeypatch):
    module, controller, _, _, _, time, calls, _, healthy, halted = clock_halt(tmp_path)
    time[0] = healthy.observed_at
    monkeypatch.setattr(module, "MAX_TRANSITIONS", halted.revision + 2)
    result = controller.recover_clock(request(module, halted))
    assert result.code == "CAPACITY" and result.health == halted and len(calls) == 1


def test_busy_recovery_and_disabled_recovery_do_not_capture(tmp_path):
    module, controller, _, _, _, time, calls, _, healthy, halted = clock_halt(tmp_path)
    time[0] = healthy.observed_at
    recovery = request(module, halted)
    assert controller._dispatch_lock.acquire(blocking=False)
    try:
        assert controller.recover_clock(recovery).code == "BUSY"
    finally:
        controller._dispatch_lock.release()
    controller.schedule = controller.schedule.model_copy(update={"enabled": False})
    assert controller.recover_clock(recovery).state == "DISABLED" and len(calls) == 1


def test_failed_resume_write_preserves_audited_halt(tmp_path, monkeypatch):
    import sqlite3

    module, controller, _, _, _, time, calls, _, healthy, halted = clock_halt(tmp_path)
    time[0] = healthy.observed_at

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("private state location")

    monkeypatch.setattr(controller, "_save", unavailable)
    result = controller.recover_clock(request(module, halted))
    assert result.code == "STORAGE_UNAVAILABLE" and result.health is None
    assert controller.tick() == halted and len(calls) == 1


@pytest.mark.parametrize("revision", [True, 0, -1, "3", 3001])
def test_clock_recovery_request_rejects_invalid_revision(revision):
    from pydantic import ValidationError
    from ai_quant_os.mt5_sync_controller import SyncClockRecoveryRequest

    with pytest.raises(ValidationError):
        SyncClockRecoveryRequest(expected_revision=revision)


def test_clock_cli_requires_revision_before_state_access(tmp_path, capsys):
    import json
    from ai_quant_os.mt5_sync_service import main

    assert (
        main(
            [
                "--config",
                str(tmp_path / "unused.json"),
                "--state-dir",
                str(tmp_path / "state"),
                "--resume-clock-halt",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "RECOVERY_INVALID"
    assert not (tmp_path / "state").exists()


def test_cli_cannot_combine_two_recovery_actions(tmp_path):
    from uuid import uuid4
    from ai_quant_os.mt5_sync_service import main

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--config",
                str(tmp_path / "unused.json"),
                "--state-dir",
                str(tmp_path / "state"),
                "--resume-clock-halt",
                "--recover-pending-attempt",
                str(uuid4()),
                "--expected-revision",
                "2",
            ]
        )
    assert error.value.code == 2 and not (tmp_path / "state").exists()

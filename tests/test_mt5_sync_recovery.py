from datetime import timedelta
from uuid import uuid4

import pytest

from test_mt5_sync_controller import setup


def halted_projection(tmp_path, monkeypatch, *, already_applied=False):
    module, controller, gateway, projection, native, time, calls, policy = setup(tmp_path)
    from ai_quant_os.mt5_sync_projection import ProjectionResult

    apply = projection.apply
    count = [0]

    def interrupted(*args):
        count[0] += 1
        if count[0] == 2:
            if already_applied:
                apply(*args)
            return ProjectionResult(
                state="WITHHELD", code="STORAGE_UNAVAILABLE", attempt_id=args[2]
            )
        return apply(*args)

    monkeypatch.setattr(projection, "apply", interrupted)
    halted = controller.tick()
    assert halted.state == "HALTED" and halted.code == "PROJECTION_FAILURE"
    monkeypatch.setattr(projection, "apply", apply)
    return module, controller, gateway, projection, native, time, calls, policy, halted


def recovery_request(module, halted, **changes):
    return module.SyncRecoveryRequest(
        expected_revision=halted.revision,
        pending_attempt_id=halted.pending_attempt_id,
        **changes,
    )


def test_recovery_imports_existing_receipt_without_another_capture(tmp_path, monkeypatch):
    module, controller, _, projection, _, _, calls, _, halted = halted_projection(
        tmp_path, monkeypatch
    )
    result = controller.recover_pending(recovery_request(module, halted))
    assert result.state == "RECOVERED" and result.code is None
    assert result.health.state == "HEALTHY" and result.health.revision == halted.revision + 1
    assert result.health.pending_attempt_id is None
    assert result.health.last_history_end == halted.pending_history_end
    assert projection.view(controller.connection).receipts == 1 and len(calls) == 1
    assert result.may_submit_orders is False and result.may_affect_portfolio is False


def test_already_projected_receipt_recovers_after_expiry_and_restart(tmp_path, monkeypatch):
    module, controller, gateway, projection, _, time, calls, policy, halted = halted_projection(
        tmp_path, monkeypatch, already_applied=True
    )
    controller.close()
    time[0] += timedelta(seconds=4)
    reopened = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        controller.connection,
        policy,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    result = reopened.recover_pending(recovery_request(module, halted))
    assert result.state == "RECOVERED" and len(calls) == 1
    assert result.health.last_success_at == time[0]
    assert result.health.last_history_end == halted.pending_history_end
    assert projection.view(controller.connection).receipts == 1
    reopened.close()
    again = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        controller.connection,
        policy,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    assert again.tick() == result.health and len(calls) == 1


@pytest.mark.parametrize("conflict", ["revision", "attempt"])
def test_stale_or_wrong_recovery_identity_does_not_mutate(tmp_path, monkeypatch, conflict):
    module, controller, _, projection, _, _, calls, _, halted = halted_projection(
        tmp_path, monkeypatch
    )
    request = module.SyncRecoveryRequest(
        expected_revision=halted.revision + (1 if conflict == "revision" else 0),
        pending_attempt_id=uuid4() if conflict == "attempt" else halted.pending_attempt_id,
    )
    result = controller.recover_pending(request)
    assert result.code == "STATE_CONFLICT"
    assert controller.tick() == halted and len(calls) == 1
    assert projection.view(controller.connection) is None


def test_uncertain_custody_cannot_be_recovered_by_dispatching(tmp_path):
    from ai_quant_os.mt5_demo_worker import SupervisedSync

    module, controller, gateway, _, _, _, _, _ = setup(tmp_path)
    calls = []

    def uncertain(request, *, clock, worker_id):
        calls.append(worker_id)
        return SupervisedSync(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway.runner = uncertain
    halted = controller.tick()
    result = controller.recover_pending(recovery_request(module, halted))
    assert result.code == "SOURCE_WITHHELD" and result.health == halted
    assert controller.tick() == halted and len(calls) == 1


def test_recovery_cannot_import_expired_unprojected_facts(tmp_path, monkeypatch):
    module, controller, _, projection, _, time, calls, _, halted = halted_projection(
        tmp_path, monkeypatch
    )
    time[0] += timedelta(seconds=4)
    result = controller.recover_pending(recovery_request(module, halted))
    assert result.code == "SOURCE_WITHHELD" and controller.tick() == halted
    assert projection.view(controller.connection) is None and len(calls) == 1


def test_recovery_clock_regression_retains_pending_intent(tmp_path, monkeypatch):
    module, controller, _, projection, _, time, calls, _, halted = halted_projection(
        tmp_path, monkeypatch
    )
    time[0] -= timedelta(seconds=1)
    result = controller.recover_pending(recovery_request(module, halted))
    assert result.code == "CLOCK_REGRESSION" and result.health == halted
    assert projection.view(controller.connection) is None and len(calls) == 1


@pytest.mark.parametrize("revision", [True, 0, -1, "2", 3001])
def test_recovery_request_rejects_invalid_revision(revision):
    from pydantic import ValidationError
    from ai_quant_os.mt5_sync_controller import SyncRecoveryRequest

    with pytest.raises(ValidationError):
        SyncRecoveryRequest(expected_revision=revision, pending_attempt_id=uuid4())


def test_recovery_repeat_conflicts_without_duplicate_projection(tmp_path, monkeypatch):
    module, controller, _, projection, _, _, calls, _, halted = halted_projection(
        tmp_path, monkeypatch
    )
    request = recovery_request(module, halted)
    first = controller.recover_pending(request)
    second = controller.recover_pending(request)
    assert second.code == "STATE_CONFLICT" and second.health == first.health
    assert controller.tick() == first.health and len(calls) == 1
    assert projection.view(controller.connection).receipts == 1


def test_controller_write_failure_retains_halt_and_recovery_can_resume(tmp_path, monkeypatch):
    import sqlite3

    module, controller, _, projection, _, time, calls, _, halted = halted_projection(
        tmp_path, monkeypatch
    )
    save = controller._save

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("private controller error")

    monkeypatch.setattr(controller, "_save", unavailable)
    request = recovery_request(module, halted)
    assert controller.recover_pending(request).code == "STORAGE_UNAVAILABLE"
    assert controller.tick() == halted and projection.view(controller.connection).receipts == 1
    monkeypatch.setattr(controller, "_save", save)
    time[0] += timedelta(seconds=4)
    assert controller.recover_pending(request).state == "RECOVERED" and len(calls) == 1


def test_recovery_cli_requires_existing_state_before_creating_any_directory(tmp_path, capsys):
    from ai_quant_os.mt5_sync_service import main
    from test_mt5_sync_service import config_document
    import json

    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_document(False)), encoding="utf-8")
    state = tmp_path / "missing-state"
    assert (
        main(
            [
                "--config",
                str(config),
                "--state-dir",
                str(state),
                "--recover-pending-attempt",
                str(uuid4()),
                "--expected-revision",
                "2",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "RECOVERY_STATE_REQUIRED"
    assert not state.exists()


def test_recovery_cli_finishes_existing_projection_without_starting_loop(
    tmp_path,
    monkeypatch,
    capsys,
):
    import json
    from ai_quant_os import mt5_sync_service
    from test_mt5_sync_service import config_document

    _, controller, gateway, projection, _, _, calls, policy, halted = halted_projection(
        tmp_path, monkeypatch, already_applied=True
    )
    controller.close()
    gateway.close()
    projection.close()
    config = tmp_path / "config.json"
    values = config_document()
    values["schedule"] = policy.model_dump(mode="json")
    config.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.setenv("AI_QUANT_MT5_PASSWORD", "fixture-private-password")
    monkeypatch.setattr(mt5_sync_service.sys, "platform", "win32")

    def forbidden(*args, **kwargs):
        raise AssertionError("Recovery must not start the service loop")

    monkeypatch.setattr(mt5_sync_service, "run_service", forbidden)
    assert (
        mt5_sync_service.main(
            [
                "--config",
                str(config),
                "--state-dir",
                str(tmp_path),
                "--enable-demo-service",
                "--recover-pending-attempt",
                str(halted.pending_attempt_id),
                "--expected-revision",
                str(halted.revision),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "RECOVERED" and result["health"]["state"] == "HEALTHY"
    assert result["may_submit_orders"] is False and len(calls) == 1
    assert not (tmp_path / "health.json").exists()


def test_recovery_wire_schema_matches_runtime_and_denies_order_authority():
    import json
    from pathlib import Path
    from pydantic import TypeAdapter
    from jsonschema import Draft202012Validator, ValidationError
    from ai_quant_os.mt5_sync_controller import SyncRecoveryRequest, SyncRecoveryResult

    path = Path(__file__).resolve().parents[1] / "docs/schemas/demo-sync-recovery.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema == TypeAdapter(SyncRecoveryRequest | SyncRecoveryResult).json_schema()
    result = SyncRecoveryResult(
        state="WITHHELD",
        code="BUSY",
        request=SyncRecoveryRequest(expected_revision=2, pending_attempt_id=uuid4()),
    ).model_dump(mode="json")
    validator = Draft202012Validator(schema)
    validator.validate(result)
    result["may_submit_orders"] = True
    with pytest.raises(ValidationError):
        validator.validate(result)


def test_disabled_recovery_never_dispatches(tmp_path):
    module, controller, _, _, _, _, calls, _ = setup(tmp_path, enabled=False)
    request = module.SyncRecoveryRequest(expected_revision=1, pending_attempt_id=uuid4())
    assert controller.recover_pending(request).state == "DISABLED" and calls == []


def test_busy_recovery_does_not_wait_or_modify_halt(tmp_path, monkeypatch):
    module, controller, _, projection, _, _, calls, _, halted = halted_projection(
        tmp_path, monkeypatch
    )
    assert controller._dispatch_lock.acquire(blocking=False)
    try:
        assert controller.recover_pending(recovery_request(module, halted)).code == "BUSY"
    finally:
        controller._dispatch_lock.release()
    assert controller.tick() == halted and projection.view(controller.connection) is None
    assert len(calls) == 1


def test_capacity_recovery_withholds_before_projection(tmp_path, monkeypatch):
    module, controller, _, projection, _, _, calls, _, halted = halted_projection(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(module, "MAX_TRANSITIONS", halted.revision + 2)
    result = controller.recover_pending(recovery_request(module, halted))
    assert result.code == "CAPACITY" and result.health == halted
    assert projection.view(controller.connection) is None and len(calls) == 1


def test_corrupt_controller_recovery_never_imports_receipt(tmp_path, monkeypatch):
    import sqlite3

    module, controller, _, projection, _, _, calls, _, halted = halted_projection(
        tmp_path, monkeypatch
    )
    with sqlite3.connect(tmp_path / "controller.sqlite") as db:
        db.execute("DELETE FROM audit_events WHERE event_key='1'")
    result = controller.recover_pending(recovery_request(module, halted))
    assert result.code == "STORAGE_UNAVAILABLE" and result.health is None
    assert projection.view(controller.connection) is None and len(calls) == 1


def test_account_mismatch_recovery_keeps_halt_and_receipt(tmp_path):
    module, controller, _, _, native, _, calls, _ = setup(tmp_path)
    native.account.login += 1
    halted = controller.tick()
    result = controller.recover_pending(recovery_request(module, halted))
    assert result.code == "SOURCE_WITHHELD" and result.health == halted
    assert controller.tick() == halted and len(calls) == 1


def test_history_gap_recovery_cannot_replace_original_coverage(tmp_path):
    module, controller, _, _, _, time, calls, _ = setup(tmp_path)
    time[0] += timedelta(days=2)
    halted = controller.tick()
    assert halted.code == "HISTORY_GAP" and halted.pending_attempt_id is None
    request = module.SyncRecoveryRequest(
        expected_revision=halted.revision,
        pending_attempt_id=uuid4(),
    )
    result = controller.recover_pending(request)
    assert result.code == "STATE_CONFLICT" and result.health == halted and calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["--expected-revision", "2"],
        ["--recover-pending-attempt", "invalid"],
        ["--recover-pending-attempt", str(uuid4()), "--expected-revision", "0"],
    ],
)
def test_invalid_cli_recovery_never_creates_state(tmp_path, capsys, arguments):
    import json
    from ai_quant_os.mt5_sync_service import main

    state = tmp_path / "state"
    assert (
        main(
            [
                "--config",
                str(tmp_path / "unused.json"),
                "--state-dir",
                str(state),
                *arguments,
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "RECOVERY_INVALID"
    assert not state.exists()

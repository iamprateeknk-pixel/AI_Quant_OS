from datetime import timedelta
from uuid import uuid4

from test_mt5_sync_controller import fresh, setup


def history_gap(tmp_path):
    module, controller, gateway, projection, native, time, calls, policy = setup(tmp_path)
    original = time[0]
    time[0] += timedelta(days=2)
    fresh(native, time)
    native.history_deals_get = lambda start, end: tuple(
        deal
        for deal in native.deals
        if start.timestamp() * 1000 <= deal.time_msc <= end.timestamp() * 1000
    )
    halted = controller.tick()
    assert halted.code == "HISTORY_GAP" and calls == []
    return module, controller, gateway, projection, native, time, calls, policy, original, halted


def request(module, halted, attempt=None):
    return module.SyncBackfillRequest(
        expected_revision=halted.revision, attempt_id=attempt or uuid4()
    )


def test_backfill_captures_oldest_bounded_window_without_skipping(tmp_path):
    module, controller, _, projection, _, _, calls, policy, original, halted = history_gap(tmp_path)
    result = controller.backfill_history(request(module, halted))
    assert result.state == "COMPLETED" and result.health.state == "HEALTHY"
    view = projection.view(controller.connection)
    assert view.batch.history_start == policy.initial_history_start
    assert view.batch.history_end == policy.initial_history_start + timedelta(days=1)
    assert result.health.last_history_end == view.batch.history_end
    assert result.health.last_history_end < original + timedelta(days=2)
    assert view.known_deals == 2 and len(calls) == 1
    assert result.may_submit_orders is False and result.may_affect_portfolio is False


def test_repeated_backfill_then_normal_tick_covers_gap_with_overlap(tmp_path):
    module, controller, _, projection, native, time, calls, policy, _, halted = history_gap(
        tmp_path
    )
    first = controller.backfill_history(request(module, halted))
    time[0] = first.health.next_due_at
    fresh(native, time)
    second_halt = controller.tick()
    assert second_halt.code == "HISTORY_GAP" and len(calls) == 1
    second = controller.backfill_history(request(module, second_halt))
    assert second.state == "COMPLETED"
    second_view = projection.view(controller.connection)
    assert second_view.batch.history_start == first.health.last_history_end - timedelta(seconds=60)
    time[0] = second.health.next_due_at
    fresh(native, time)
    normal = controller.tick()
    assert normal.state == "HEALTHY" and normal.last_history_end == time[0] and len(calls) == 3
    view = projection.view(controller.connection)
    assert view.history_start == policy.initial_history_start and view.history_through == time[0]
    assert view.known_deals == 2


def test_stale_revision_never_dispatches_backfill(tmp_path):
    module, controller, _, projection, _, _, calls, _, _, halted = history_gap(tmp_path)
    result = controller.backfill_history(
        module.SyncBackfillRequest(expected_revision=halted.revision + 1, attempt_id=uuid4())
    )
    assert result.code == "STATE_CONFLICT" and result.health == halted and calls == []
    assert projection.view(controller.connection) is None


def test_repeated_completed_request_does_not_capture_again(tmp_path):
    module, controller, _, projection, _, _, calls, _, _, halted = history_gap(tmp_path)
    command = request(module, halted)
    first = controller.backfill_history(command)
    repeat = controller.backfill_history(command)
    assert repeat.code == "STATE_CONFLICT" and repeat.health == first.health
    assert projection.view(controller.connection).receipts == 1 and len(calls) == 1


def test_restart_after_backfill_intent_uses_original_attempt(tmp_path, monkeypatch):
    module, controller, gateway, projection, _, time, calls, policy, _, halted = history_gap(
        tmp_path
    )
    command = request(module, halted)

    def crash(*args):
        raise RuntimeError("fixture interrupted dispatch")

    monkeypatch.setattr(controller, "_capture_pending", crash)
    assert controller.backfill_history(command).code == "STORAGE_UNAVAILABLE" and calls == []
    controller.close()
    reopened = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        controller.connection,
        policy,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    assert reopened.tick().state == "HEALTHY" and len(calls) == 1
    assert projection.view(controller.connection).latest_attempt_id == command.attempt_id


def test_uncertain_backfill_retains_identity_and_never_retries(tmp_path):
    from ai_quant_os.mt5_demo_worker import SupervisedSync

    module, controller, gateway, _, _, time, _, _, _, halted = history_gap(tmp_path)
    captured = []

    def uncertain(request, *, clock, worker_id):
        captured.append(worker_id)
        return SupervisedSync(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway.runner = uncertain
    command = request(module, halted)
    result = controller.backfill_history(command)
    assert (
        result.code == "CUSTODY_UNCERTAIN"
        and result.health.pending_attempt_id == command.attempt_id
    )
    time[0] += timedelta(hours=1)
    assert controller.tick() == result.health and len(captured) == 1
    assert controller.backfill_history(command).code == "STATE_CONFLICT" and len(captured) == 1


def test_prior_backfill_uuid_cannot_be_reused_for_another_window(tmp_path):
    module, controller, _, _, native, time, calls, _, _, halted = history_gap(tmp_path)
    command = request(module, halted)
    first = controller.backfill_history(command)
    time[0] = first.health.next_due_at
    fresh(native, time)
    second_halt = controller.tick()
    result = controller.backfill_history(request(module, second_halt, command.attempt_id))
    assert result.code == "ATTEMPT_CONFLICT" and result.health == second_halt and len(calls) == 1


def test_failed_backfill_intent_never_dispatches(tmp_path, monkeypatch):
    import sqlite3

    module, controller, _, _, _, _, calls, _, _, halted = history_gap(tmp_path)

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("private location")

    monkeypatch.setattr(controller, "_save", unavailable)
    result = controller.backfill_history(request(module, halted))
    assert result.code == "STORAGE_UNAVAILABLE" and calls == [] and controller.tick() == halted


def test_backfill_cli_runs_one_explicit_window_without_service_timer(tmp_path, monkeypatch, capsys):
    import json
    from ai_quant_os import mt5_sync_service
    from test_mt5_sync_service import config_document

    module, controller, gateway, projection, _, time, calls, policy, _, halted = history_gap(
        tmp_path
    )
    controller.close()
    projection.close()
    values = config_document()
    values["schedule"] = policy.model_dump(mode="json")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.setenv("AI_QUANT_MT5_PASSWORD", "fixture-private-password")
    monkeypatch.setattr(mt5_sync_service.sys, "platform", "win32")
    original = module.DemoSyncController
    projection_type = mt5_sync_service.SyncProjectionStore

    def fixed_projection_clock(path):
        return projection_type(path, clock=lambda: time[0])

    def fixed_clock(*args, **kwargs):
        return original(*args, **kwargs, clock=lambda: time[0])

    def forbidden(*args, **kwargs):
        raise AssertionError("One-shot backfill must not start a service loop")

    monkeypatch.setattr(mt5_sync_service, "DemoSyncController", fixed_clock)
    monkeypatch.setattr(mt5_sync_service, "DemoObservationGateway", lambda path: gateway)
    monkeypatch.setattr(mt5_sync_service, "SyncProjectionStore", fixed_projection_clock)
    monkeypatch.setattr(mt5_sync_service, "run_service", forbidden)
    command = request(module, halted)
    assert (
        mt5_sync_service.main(
            [
                "--config",
                str(config),
                "--state-dir",
                str(tmp_path),
                "--enable-demo-service",
                "--backfill-history-attempt",
                str(command.attempt_id),
                "--expected-revision",
                str(command.expected_revision),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "COMPLETED" and result["health"]["state"] == "HEALTHY"
    assert result["request"]["attempt_id"] == str(command.attempt_id) and len(calls) == 1
    assert not (tmp_path / "health.json").exists()


def test_backfill_wire_contract_matches_runtime_and_denies_orders():
    import json
    from pathlib import Path
    import pytest
    from pydantic import TypeAdapter
    from jsonschema import Draft202012Validator, ValidationError
    from ai_quant_os.mt5_sync_controller import SyncBackfillRequest, SyncBackfillResult

    path = Path(__file__).resolve().parents[1] / "docs/schemas/demo-sync-backfill.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema == TypeAdapter(SyncBackfillRequest | SyncBackfillResult).json_schema()
    result = SyncBackfillResult(
        state="WITHHELD",
        code="BUSY",
        request=SyncBackfillRequest(expected_revision=1, attempt_id=uuid4()),
    ).model_dump(mode="json")
    validator = Draft202012Validator(schema)
    validator.validate(result)
    result["may_submit_orders"] = True
    with pytest.raises(ValidationError):
        validator.validate(result)


def test_disabled_backfill_and_busy_backfill_do_not_dispatch(tmp_path):
    module, controller, _, _, _, _, calls, _, _, halted = history_gap(tmp_path)
    command = request(module, halted)
    assert controller._dispatch_lock.acquire(blocking=False)
    try:
        assert controller.backfill_history(command).code == "BUSY"
    finally:
        controller._dispatch_lock.release()
    controller.schedule = controller.schedule.model_copy(update={"enabled": False})
    assert controller.backfill_history(command).state == "DISABLED" and calls == []


def test_backfill_clock_regression_and_capacity_preserve_halt(tmp_path, monkeypatch):
    module, controller, _, _, _, time, calls, _, _, halted = history_gap(tmp_path)
    time[0] -= timedelta(seconds=1)
    assert controller.backfill_history(request(module, halted)).code == "CLOCK_REGRESSION"
    time[0] = halted.observed_at
    monkeypatch.setattr(module, "MAX_TRANSITIONS", halted.revision + 2)
    assert controller.backfill_history(request(module, halted)).code == "CAPACITY"
    assert controller.tick() == halted and calls == []


def test_unfinished_gateway_blocks_backfill_before_controller_intent(tmp_path):
    from ai_quant_os.mt5_demo_worker import SupervisedSync
    from test_mt5_account_sync import sync_request

    module, controller, gateway, _, _, _, calls, _, _, halted = history_gap(tmp_path)

    def uncertain(request, *, clock, worker_id):
        return SupervisedSync(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway.runner = uncertain
    gateway.capture(sync_request(), uuid4())
    result = controller.backfill_history(request(module, halted))
    assert result.code == "CUSTODY_UNCERTAIN" and result.health == halted and calls == []
    assert controller.tick() == halted


def test_backfill_corrupt_projection_is_withheld_without_capture(tmp_path):
    import sqlite3

    module, controller, _, _, _, _, calls, _, _, halted = history_gap(tmp_path)
    with sqlite3.connect(tmp_path / "projection.sqlite") as db:
        db.execute("DELETE FROM audit_head")
    result = controller.backfill_history(request(module, halted))
    assert result.code == "PROJECTION_FAILURE" and result.health == halted and calls == []


def test_backfill_corrupt_controller_is_withheld_without_capture(tmp_path):
    import sqlite3

    module, controller, _, _, _, _, calls, _, _, halted = history_gap(tmp_path)
    with sqlite3.connect(tmp_path / "controller.sqlite") as db:
        db.execute("DELETE FROM audit_events")
    result = controller.backfill_history(request(module, halted))
    assert result.code == "STORAGE_UNAVAILABLE" and result.health is None and calls == []


def test_backfill_cli_requires_paired_identity_and_revision_before_state(tmp_path, capsys):
    import json
    from ai_quant_os.mt5_sync_service import main

    assert (
        main(
            [
                "--config",
                str(tmp_path / "unused.json"),
                "--state-dir",
                str(tmp_path / "state"),
                "--backfill-history-attempt",
                str(uuid4()),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "RECOVERY_INVALID"
    assert not (tmp_path / "state").exists()


def test_backfill_request_strict_identity_and_revision():
    import pytest
    from uuid import uuid1
    from pydantic import ValidationError
    from ai_quant_os.mt5_sync_controller import SyncBackfillRequest

    for revision in (True, 0, -1, "1", 3001):
        with pytest.raises(ValidationError):
            SyncBackfillRequest(expected_revision=revision, attempt_id=uuid4())
    with pytest.raises(ValidationError):
        SyncBackfillRequest(expected_revision=1, attempt_id=uuid1())

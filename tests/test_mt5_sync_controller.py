import importlib.util
import sqlite3
from types import SimpleNamespace
from datetime import timedelta

from test_mt5_account_sync import NOW, SyncNative, sync_request
from test_mt5_sync_projection import fixture_gateway


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_sync_controller") is not None
    from ai_quant_os import mt5_sync_controller

    return mt5_sync_controller


def setup(tmp_path, *, enabled=True):
    module = implementation()
    from ai_quant_os.mt5_sync_projection import SyncProjectionStore

    time = [NOW]
    native = SyncNative()
    gateway, calls = fixture_gateway(tmp_path / "gateway.sqlite", native, time)
    projection = SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])
    policy = module.SyncSchedule(
        enabled=enabled,
        initial_history_start=NOW - timedelta(hours=1),
        interval_seconds=10,
        overlap_seconds=60,
        max_backoff_seconds=80,
    )
    controller = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        sync_request().connection,
        policy,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    return module, controller, gateway, projection, native, time, calls, policy


def fresh(native, time):
    native.tick.time_msc = int(time[0].timestamp() * 1000)


def test_disabled_schedule_does_not_dispatch(tmp_path):
    _, controller, _, _, _, _, calls, _ = setup(tmp_path, enabled=False)
    assert controller.tick().state == "DISABLED" and calls == []


def test_due_capture_projects_and_not_due_tick_never_dispatches(tmp_path):
    _, controller, _, projection, _, _, calls, _ = setup(tmp_path)
    result = controller.tick()
    assert result.state == "HEALTHY" and result.last_history_end == NOW
    assert result.next_due_at == NOW + timedelta(seconds=10)
    assert projection.view(sync_request().connection).receipts == 1
    assert controller.tick() == result and len(calls) == 1


def test_restart_retains_due_time_and_advances_with_overlap(tmp_path):
    module, controller, gateway, projection, native, time, calls, policy = setup(tmp_path)
    controller.tick()
    controller.close()
    reopened = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        sync_request().connection,
        policy,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    assert reopened.tick().state == "HEALTHY" and len(calls) == 1
    time[0] += timedelta(seconds=10)
    fresh(native, time)
    assert reopened.tick().state == "HEALTHY"
    view = projection.view(sync_request().connection)
    assert view.batch.history_start == NOW - timedelta(seconds=60)
    assert view.receipts == 2 and view.known_deals == 2


def test_disconnect_then_reconnect_uses_new_due_attempt_with_same_coverage(tmp_path):
    _, controller, _, projection, native, time, calls, _ = setup(tmp_path)
    connected = [False]
    native.terminal_info = lambda: SimpleNamespace(connected=connected[0])
    first = controller.tick()
    assert first.state == "DEGRADED" and first.consecutive_failures == 1
    assert first.last_history_end is None
    assert controller.tick() == first and len(calls) == 1
    connected[0] = True
    time[0] = first.next_due_at
    fresh(native, time)
    result = controller.tick()
    assert result.state == "HEALTHY" and result.consecutive_failures == 0 and len(calls) == 2
    assert projection.view(sync_request().connection).history_start == NOW - timedelta(hours=1)


def test_clock_regression_halts_without_capture(tmp_path):
    _, controller, _, _, _, time, calls, _ = setup(tmp_path)
    controller.tick()
    time[0] -= timedelta(seconds=1)
    result = controller.tick()
    assert result.state == "HALTED" and result.code == "CLOCK_REGRESSION" and len(calls) == 1


def test_disconnected_history_gap_halts_without_truncating_window(tmp_path):
    _, controller, _, _, _, time, calls, _ = setup(tmp_path)
    time[0] += timedelta(days=2)
    result = controller.tick()
    assert result.state == "HALTED" and result.code == "HISTORY_GAP" and calls == []


def test_corrupt_schedule_never_dispatches(tmp_path):
    module, controller, gateway, projection, _, time, calls, policy = setup(tmp_path)
    controller.tick()
    controller.close()
    with sqlite3.connect(tmp_path / "controller.sqlite") as connection:
        connection.execute("DELETE FROM audit_events")
    reopened = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        sync_request().connection,
        policy,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    assert reopened.tick().code == "STORAGE_UNAVAILABLE" and len(calls) == 1


def test_schedule_change_cannot_reuse_existing_controller(tmp_path):
    module, controller, gateway, projection, _, time, calls, policy = setup(tmp_path)
    controller.close()
    changed = policy.model_copy(update={"interval_seconds": 20})
    reopened = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        sync_request().connection,
        changed,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    assert reopened.tick().code == "STORAGE_UNAVAILABLE" and calls == []


def test_crash_after_projection_commit_resumes_original_attempt_after_expiry(tmp_path, monkeypatch):
    module, controller, gateway, projection, _, time, calls, policy = setup(tmp_path)
    save = controller._save

    def fail_completion(old, **changes):
        if changes.get("state") == "HEALTHY":
            raise sqlite3.OperationalError("fixture controller completion failure")
        return save(old, **changes)

    monkeypatch.setattr(controller, "_save", fail_completion)
    assert controller.tick().code == "STORAGE_UNAVAILABLE"
    view = projection.view(sync_request().connection)
    assert view.receipts == 1
    controller.close()
    time[0] += timedelta(seconds=4)
    reopened = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        sync_request().connection,
        policy,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    result = reopened.tick()
    assert result.state == "HEALTHY" and result.last_history_end == NOW
    assert len(calls) == 1 and projection.view(sync_request().connection).receipts == 1


def test_uncertain_worker_halts_and_retains_attempt_without_retry(tmp_path):
    from ai_quant_os.mt5_demo_worker import SupervisedSync

    _, controller, gateway, _, _, time, _, _ = setup(tmp_path)
    calls = []

    def uncertain(request, *, clock, worker_id):
        calls.append(worker_id)
        return SupervisedSync(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway.runner = uncertain
    first = controller.tick()
    assert first.state == "HALTED" and first.code == "CUSTODY_UNCERTAIN"
    assert first.pending_attempt_id is not None
    time[0] += timedelta(hours=1)
    assert controller.tick() == first and len(calls) == 1


def test_overlapping_timer_call_observes_active_capture_without_halting(tmp_path):
    from threading import Event, Thread

    _, controller, gateway, _, _, _, calls, _ = setup(tmp_path)
    entered, release = Event(), Event()
    runner = gateway.runner
    results = []

    def controlled(request, *, clock, worker_id):
        entered.set()
        assert release.wait(5)
        return runner(request, clock=clock, worker_id=worker_id)

    gateway.runner = controlled
    thread = Thread(target=lambda: results.append(controller.tick()))
    thread.start()
    try:
        assert entered.wait(5)
        concurrent = controller.tick()
        assert concurrent.state == "ACTIVE" and concurrent.code is None
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert results[0].state == "HEALTHY" and len(calls) == 1


def test_wrong_account_halts_instead_of_scheduling_reconnect(tmp_path):
    _, controller, _, _, native, time, calls, _ = setup(tmp_path)
    native.account.login += 1
    result = controller.tick()
    assert result.state == "HALTED" and result.code == "SOURCE_FAILURE"
    time[0] += timedelta(hours=1)
    assert controller.tick() == result and len(calls) == 1


def test_exported_controller_contract_matches_runtime_and_denies_orders():
    import json
    from pathlib import Path
    import pytest
    from jsonschema import Draft202012Validator, ValidationError
    from pydantic import TypeAdapter

    module = implementation()
    path = Path(__file__).resolve().parents[1] / "docs/schemas/demo-sync-controller.schema.json"
    assert path.is_file()
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema == TypeAdapter(module.SyncSchedule | module.SyncHealth).json_schema()
    health = module.SyncHealth(state="STARTING", observed_at=NOW, next_due_at=NOW).model_dump(
        mode="json"
    )
    validator = Draft202012Validator(schema)
    validator.validate(health)
    health["may_submit_orders"] = True
    with pytest.raises(ValidationError):
        validator.validate(health)


def test_backoff_is_capped_and_does_not_advance_failed_history(tmp_path):
    _, controller, _, _, native, time, calls, _ = setup(tmp_path)
    native.terminal_info = lambda: SimpleNamespace(connected=False)
    for failure, delay in enumerate([20, 40, 80, 80], start=1):
        result = controller.tick()
        assert result.consecutive_failures == failure and result.last_history_end is None
        assert result.next_due_at - time[0] == timedelta(seconds=delay)
        time[0] = result.next_due_at
    assert len(calls) == 4


def test_transition_capacity_reserves_room_for_halt_before_dispatch(tmp_path, monkeypatch):
    module, controller, _, _, _, _, calls, _ = setup(tmp_path)
    monkeypatch.setattr(module, "MAX_TRANSITIONS", 2)
    result = controller.tick()
    assert result.state == "HALTED" and result.code == "CAPACITY" and calls == []


def test_failed_intent_write_never_dispatches_worker(tmp_path, monkeypatch):
    _, controller, _, _, _, _, calls, _ = setup(tmp_path)

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("fixture intent failure")

    monkeypatch.setattr(controller, "_save", unavailable)
    assert controller.tick().code == "STORAGE_UNAVAILABLE" and calls == []

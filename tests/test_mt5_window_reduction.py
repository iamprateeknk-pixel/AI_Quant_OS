from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from ai_quant_os.mt5_account_sync import DemoSyncRequest
from test_mt5_history_backfill import history_gap, request as backfill_request


def capacity_halt(tmp_path):
    module, controller, gateway, projection, native, time, calls, policy, _, gap = history_gap(
        tmp_path
    )
    base = native.deals[0]
    native.deals = []
    for index in range(17):
        deal = deepcopy(base)
        deal.ticket = 2000 + index
        deal.time_msc = int(
            (policy.initial_history_start + timedelta(minutes=5 + 10 * index)).timestamp() * 1000
        )
        native.deals.append(deal)
    command = backfill_request(module, gap)
    failed = controller.backfill_history(command)
    assert (
        failed.code == "SOURCE_FAILURE" and failed.health.pending_attempt_id == command.attempt_id
    )
    assert (
        gateway.replay_existing(
            DemoSyncRequest(
                connection=controller.connection,
                history_start=failed.health.pending_history_start,
                history_end=failed.health.pending_history_end,
            ),
            command.attempt_id,
        ).report.code
        == "CAPACITY"
    )
    return module, controller, gateway, projection, native, time, calls, policy, failed.health


def request(module, halted, **changes):
    values = dict(
        expected_revision=halted.revision,
        prior_attempt_id=halted.pending_attempt_id,
        attempt_id=uuid4(),
        window_seconds=3600,
    )
    values.update(changes)
    return module.SyncWindowReductionRequest(**values)


def test_smaller_window_preserves_oldest_start_and_failed_receipt(tmp_path):
    module, controller, gateway, projection, _, _, calls, _, halted = capacity_halt(tmp_path)
    command = request(module, halted)
    result = controller.reduce_failed_window(command)
    assert result.state == "COMPLETED" and result.health.state == "HEALTHY"
    assert result.prior_receipt_ref is not None
    view = projection.view(controller.connection)
    assert view.batch.history_start == halted.pending_history_start
    assert view.batch.history_end == halted.pending_history_start + timedelta(hours=1)
    assert (
        view.known_deals == 6 and view.latest_attempt_id == command.attempt_id and len(calls) == 2
    )
    assert gateway.attempt_status(halted.pending_attempt_id) == "PRESENT"
    assert gateway.attempt_status(command.attempt_id) == "PRESENT"
    assert result.may_submit_orders is False


def test_repeated_window_reduction_does_not_dispatch_again(tmp_path):
    module, controller, _, projection, _, _, calls, _, halted = capacity_halt(tmp_path)
    command = request(module, halted)
    first = controller.reduce_failed_window(command)
    repeat = controller.reduce_failed_window(command)
    assert repeat.code == "STATE_CONFLICT" and repeat.health == first.health and len(calls) == 2
    assert projection.view(controller.connection).receipts == 1


def test_equal_or_larger_window_is_refused(tmp_path):
    module, controller, _, _, _, _, calls, _, halted = capacity_halt(tmp_path)
    command = module.SyncWindowReductionRequest(
        expected_revision=halted.revision,
        prior_attempt_id=halted.pending_attempt_id,
        attempt_id=uuid4(),
        window_seconds=86400,
    )
    result = controller.reduce_failed_window(command)
    assert result.code == "WINDOW_NOT_SMALLER" and result.health == halted and len(calls) == 1


def test_uncertain_pending_attempt_cannot_be_reduced(tmp_path):
    from ai_quant_os.mt5_demo_worker import SupervisedSync

    module, controller, gateway, _, _, _, calls, _, _, gap = history_gap(tmp_path)

    def uncertain(request, *, clock, worker_id):
        return SupervisedSync(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway.runner = uncertain
    halted = controller.backfill_history(backfill_request(module, gap)).health
    result = controller.reduce_failed_window(request(module, halted))
    assert result.code == "STATE_CONFLICT" and result.health == halted and calls == []
    assert controller.tick() == halted


def test_account_failure_cannot_be_replaced_by_a_smaller_window(tmp_path):
    from test_mt5_sync_controller import setup

    module, controller, _, _, native, _, calls, _ = setup(tmp_path)
    native.account.login += 1
    halted = controller.tick()
    result = controller.reduce_failed_window(request(module, halted, window_seconds=60))
    assert result.code == "RECEIPT_INELIGIBLE" and result.health == halted and len(calls) == 1
    assert controller.tick() == halted


def test_restart_after_replacement_intent_uses_the_new_bound_uuid(tmp_path, monkeypatch):
    module, controller, gateway, projection, _, time, calls, policy, halted = capacity_halt(
        tmp_path
    )
    command = request(module, halted)

    def interrupted(*args):
        raise RuntimeError("fixture interrupted dispatch")

    monkeypatch.setattr(controller, "_capture_pending", interrupted)
    assert (
        controller.reduce_failed_window(command).code == "STORAGE_UNAVAILABLE" and len(calls) == 1
    )
    controller.close()
    reopened = module.DemoSyncController(
        tmp_path / "controller.sqlite",
        controller.connection,
        policy,
        gateway,
        projection,
        clock=lambda: time[0],
    )
    assert reopened.tick().state == "HEALTHY" and len(calls) == 2
    assert projection.view(controller.connection).latest_attempt_id == command.attempt_id
    assert gateway.attempt_status(command.prior_attempt_id) == "PRESENT"


def test_failed_replacement_intent_preserves_original_pending_attempt(tmp_path, monkeypatch):
    import sqlite3

    module, controller, _, _, _, _, calls, _, halted = capacity_halt(tmp_path)

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("private location")

    monkeypatch.setattr(controller, "_save", unavailable)
    result = controller.reduce_failed_window(request(module, halted))
    assert result.code == "STORAGE_UNAVAILABLE" and len(calls) == 1
    assert controller.tick() == halted


def test_one_shot_cli_reduces_a_confirmed_capacity_failure(tmp_path, monkeypatch, capsys):
    import json
    from ai_quant_os import mt5_sync_service
    from test_mt5_sync_service import config_document

    module, controller, gateway, projection, _, time, calls, policy, halted = capacity_halt(
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
    controller_type, projection_type = (
        module.DemoSyncController,
        mt5_sync_service.SyncProjectionStore,
    )

    def fixed_controller(*args, **kwargs):
        return controller_type(*args, **kwargs, clock=lambda: time[0])

    def forbidden(*args, **kwargs):
        raise AssertionError("One-shot reduction must not start a timer")

    monkeypatch.setattr(mt5_sync_service, "DemoSyncController", fixed_controller)
    monkeypatch.setattr(mt5_sync_service, "DemoObservationGateway", lambda path: gateway)
    monkeypatch.setattr(
        mt5_sync_service,
        "SyncProjectionStore",
        lambda path: projection_type(path, clock=lambda: time[0]),
    )
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
                "--reduce-history-attempt",
                str(command.attempt_id),
                "--prior-failed-attempt",
                str(command.prior_attempt_id),
                "--history-window-seconds",
                "3600",
                "--expected-revision",
                str(command.expected_revision),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert (
        result["state"] == "COMPLETED"
        and result["prior_receipt_ref"] is not None
        and len(calls) == 2
    )
    assert not (tmp_path / "health.json").exists()


def test_window_reduction_wire_contract_matches_runtime_and_denies_orders():
    import json
    from pathlib import Path
    import pytest
    from pydantic import TypeAdapter
    from jsonschema import Draft202012Validator, ValidationError
    from ai_quant_os.mt5_sync_controller import (
        SyncWindowReductionRequest,
        SyncWindowReductionResult,
        SyncWindowReductionEvidence,
    )

    path = (
        Path(__file__).resolve().parents[1] / "docs/schemas/demo-sync-window-reduction.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert (
        schema
        == TypeAdapter(
            SyncWindowReductionRequest | SyncWindowReductionResult | SyncWindowReductionEvidence
        ).json_schema()
    )
    result = SyncWindowReductionResult(
        state="WITHHELD",
        code="BUSY",
        request=SyncWindowReductionRequest(
            expected_revision=1,
            prior_attempt_id=uuid4(),
            attempt_id=uuid4(),
            window_seconds=60,
        ),
    ).model_dump(mode="json")
    validator = Draft202012Validator(schema)
    validator.validate(result)
    result["may_submit_orders"] = True
    with pytest.raises(ValidationError):
        validator.validate(result)


def test_reduction_must_advance_an_existing_history_cursor(tmp_path):
    from test_mt5_sync_controller import fresh, setup

    module, controller, _, projection, native, time, calls, policy = setup(tmp_path)
    healthy = controller.tick()
    time[0] += timedelta(days=2)
    fresh(native, time)
    gap = controller.tick()
    base = deepcopy(native.deals[0])
    start = healthy.last_history_end - timedelta(seconds=policy.overlap_seconds)
    native.deals = []
    for index in range(17):
        deal = deepcopy(base)
        deal.ticket = 3000 + index
        deal.time_msc = int((start + timedelta(seconds=5 + 10 * index)).timestamp() * 1000)
        native.deals.append(deal)
    native.history_deals_get = lambda begin, end: tuple(
        deal
        for deal in native.deals
        if begin.timestamp() * 1000 <= deal.time_msc <= end.timestamp() * 1000
    )
    halted = controller.backfill_history(backfill_request(module, gap)).health
    result = controller.reduce_failed_window(request(module, halted, window_seconds=30))
    assert result.code == "WINDOW_NO_PROGRESS" and result.health == halted and len(calls) == 2
    assert controller.tick() == halted and projection.view(controller.connection).receipts == 1


def test_stale_or_wrong_prior_attempt_retains_original_intent(tmp_path):
    module, controller, _, _, _, _, calls, _, halted = capacity_halt(tmp_path)
    for changes in ({"expected_revision": halted.revision + 1}, {"prior_attempt_id": uuid4()}):
        result = controller.reduce_failed_window(request(module, halted, **changes))
        assert result.code == "STATE_CONFLICT" and result.health == halted and len(calls) == 1
    assert controller.tick() == halted


def test_clock_capacity_busy_and_disabled_reduction_do_not_capture(tmp_path, monkeypatch):
    module, controller, _, _, _, time, calls, _, halted = capacity_halt(tmp_path)
    command = request(module, halted)
    time[0] -= timedelta(seconds=1)
    assert controller.reduce_failed_window(command).code == "CLOCK_REGRESSION"
    time[0] = halted.observed_at
    assert controller._dispatch_lock.acquire(blocking=False)
    try:
        assert controller.reduce_failed_window(command).code == "BUSY"
    finally:
        controller._dispatch_lock.release()
    monkeypatch.setattr(module, "MAX_TRANSITIONS", halted.revision + 2)
    assert controller.reduce_failed_window(command).code == "CAPACITY"
    controller.schedule = controller.schedule.model_copy(update={"enabled": False})
    assert controller.reduce_failed_window(command).state == "DISABLED" and len(calls) == 1


def test_unrelated_unfinished_worker_blocks_reduction(tmp_path):
    from ai_quant_os.mt5_demo_worker import SupervisedSync

    module, controller, gateway, _, _, _, calls, _, halted = capacity_halt(tmp_path)

    def uncertain(request, *, clock, worker_id):
        return SupervisedSync(
            state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
        )

    gateway.runner = uncertain
    old_request = DemoSyncRequest(
        connection=controller.connection,
        history_start=halted.pending_history_start,
        history_end=halted.pending_history_end,
    )
    gateway.capture(old_request, uuid4())
    result = controller.reduce_failed_window(request(module, halted))
    assert result.code == "CUSTODY_UNCERTAIN" and result.health == halted and len(calls) == 1
    assert controller.tick() == halted


def test_already_used_replacement_identity_is_refused(tmp_path):
    module, controller, gateway, _, _, _, calls, _, halted = capacity_halt(tmp_path)
    existing = uuid4()
    gateway.capture(
        DemoSyncRequest(
            connection=controller.connection,
            history_start=halted.pending_history_start,
            history_end=halted.pending_history_end,
        ),
        existing,
    )
    result = controller.reduce_failed_window(request(module, halted, attempt_id=existing))
    assert result.code == "ATTEMPT_CONFLICT" and result.health == halted and len(calls) == 2


def test_reduction_request_requires_strict_window_and_distinct_uuid():
    import pytest
    from pydantic import ValidationError
    from ai_quant_os.mt5_sync_controller import SyncWindowReductionRequest

    values = dict(
        expected_revision=1, prior_attempt_id=uuid4(), attempt_id=uuid4(), window_seconds=60
    )
    for seconds in (True, 0, -1, "60", 86401):
        with pytest.raises(ValidationError):
            SyncWindowReductionRequest(**{**values, "window_seconds": seconds})
    with pytest.raises(ValidationError):
        SyncWindowReductionRequest(**{**values, "attempt_id": values["prior_attempt_id"]})


def test_partial_or_misplaced_cli_reduction_arguments_do_not_create_state(tmp_path, capsys):
    import json
    from ai_quant_os.mt5_sync_service import main

    for extras in (["--reduce-history-attempt", str(uuid4())], ["--history-window-seconds", "60"]):
        assert (
            main(
                [
                    "--config",
                    str(tmp_path / "unused.json"),
                    "--state-dir",
                    str(tmp_path / "state"),
                    *extras,
                ]
            )
            == 2
        )
        assert json.loads(capsys.readouterr().out)["code"] == "RECOVERY_INVALID"
    assert not (tmp_path / "state").exists()

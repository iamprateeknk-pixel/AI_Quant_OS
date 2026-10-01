"""Real service persistence tests; the dedicated CI gate requires a pinned CLI."""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("temporalio")
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporalio.exceptions import WorkflowAlreadyStartedError

from test_paper_recovery import recovery_fixture


def temporal_binary():
    path = Path(
        os.environ.get(
            "AI_QUANT_TEMPORAL_CLI",
            "data-local/tools/temporal-1.9.1/temporal.exe",
        )
    )
    if not path.is_file():
        if os.environ.get("AI_QUANT_REQUIRE_TEMPORAL") == "1":
            pytest.fail("Pinned Temporal CLI is required by this verification gate")
        pytest.skip("Install the pinned Temporal CLI for actual service tests")
    return str(path.resolve())


async def wait_stage(handle, stage):
    async with asyncio.timeout(20):
        while await handle.query("stage") != stage:
            await asyncio.sleep(0.05)


def test_cancelled_activity_drains_owning_state_write(tmp_path):
    from threading import Event
    from ai_quant_os.temporal_recovery import RecoveryActivities, RecoveryRequest

    async def run():
        service, execution, risk, state, broker = recovery_fixture(tmp_path)
        order_id = execution.orders[0].order_id
        entered, release = Event(), Event()
        contain = service.contain

        def interrupted_write(*args):
            result = contain(*args)
            entered.set()
            assert release.wait(5)
            return result

        with patch.object(service, "contain", side_effect=interrupted_write):
            task = asyncio.create_task(
                RecoveryActivities(service).contain(RecoveryRequest("drain-1", order_id))
            )
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            try:
                await asyncio.sleep(0.05)
                assert not task.done()
                task.cancel()
                await asyncio.sleep(0.05)
                assert not task.done()
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
        assert state.audit_event("paper-recovery:drain-1:contained") is not None

    asyncio.run(run())


def test_temporal_paper_recovery_resumes_after_server_and_worker_restart(tmp_path):
    binary = temporal_binary()

    async def run():
        from ai_quant_os.temporal_recovery import (
            PaperRecoveryWorkflow,
            RecoveryActivities,
            start_paper_recovery,
        )
        from ai_quant_os.paper_recovery import PaperRecoveryService
        from ai_quant_os.paper_state_store import PaperStateStore
        from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState
        from ai_quant_os.execution import PaperExecutionGateway
        from ai_quant_os.broker import BrokerRouter, SimulatedBroker
        from decimal import Decimal

        service, execution, risk, state, broker = recovery_fixture(tmp_path)
        order_id = execution.orders[0].order_id
        database = str(tmp_path / "temporal.sqlite3")
        arguments = dict(
            dev_server_existing_path=binary,
            dev_server_database_filename=database,
            dev_server_log_level="error",
            ui=False,
        )
        async with await WorkflowEnvironment.start_local(**arguments) as environment:
            activities = RecoveryActivities(service)
            with patch.object(broker, "lookup", return_value=None):
                async with Worker(
                    environment.client,
                    task_queue="paper-recovery-test",
                    workflows=[PaperRecoveryWorkflow],
                    activities=activities.registered(),
                ):
                    handle = await start_paper_recovery(
                        environment.client,
                        "incident-1",
                        order_id,
                        task_queue="paper-recovery-test",
                    )
                    await wait_stage(handle, "WAITING_RETRY")
                    with pytest.raises(WorkflowAlreadyStartedError):
                        await start_paper_recovery(
                            environment.client,
                            "incident-1",
                            order_id,
                            task_queue="paper-recovery-test",
                        )
            assert risk.safety_state == SafetyState.NO_NEW_RISK
            assert len(broker.orders) == 1

        # Reconstruct every application object from the same durable paper ledger.
        path = tmp_path / "paper.sqlite3"
        state = PaperStateStore(path)
        risk = RiskKernel(RiskLimits(Decimal(1000), Decimal(1500), 5))
        broker = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True, store_path=path)
        execution = PaperExecutionGateway(
            risk,
            store_path=path,
            router=BrokerRouter([broker]),
            on_uncertain_order=state.record_order_uncertainty,
        )
        service = PaperRecoveryService(execution, risk, state)
        async with await WorkflowEnvironment.start_local(**arguments) as environment:
            activities = RecoveryActivities(service)
            handle = environment.client.get_workflow_handle("paper-recovery:incident-1")
            # Signal persists before the replacement worker starts polling.
            await handle.signal("retry")
            async with Worker(
                environment.client,
                task_queue="paper-recovery-test",
                workflows=[PaperRecoveryWorkflow],
                activities=activities.registered(),
            ):
                result = await asyncio.wait_for(handle.result(), 30)
            assert result["stage"] == "REVIEW_REQUIRED"
            assert result["attempt"] == 2
            assert result["promotion_allowed"] is False
            assert result["order_status"] == "ACCEPTED"
            assert state.audit_integrity().valid
            assert state.audit_event("paper-recovery:incident-1:attempt:1") is not None
            assert state.audit_event("paper-recovery:incident-1:attempt:2") is not None
            assert risk.safety_state == state.safety_state() == SafetyState.NO_NEW_RISK
            assert len(broker.orders) == 1
            history = await handle.fetch_history()
            scheduled = [
                event.activity_task_scheduled_event_attributes.activity_type.name
                for event in history.events
                if event.HasField("activity_task_scheduled_event_attributes")
            ]
            assert scheduled.count("paper_recovery_contain") == 1
            assert scheduled.count("paper_recovery_reconcile") == 2
            timers = [
                event.timer_started_event_attributes.start_to_fire_timeout.seconds
                for event in history.events
                if event.HasField("timer_started_event_attributes")
            ]
            assert len(timers) == 1
            assert 604790 <= timers[0] <= 604800
            with pytest.raises(WorkflowAlreadyStartedError):
                await start_paper_recovery(
                    environment.client, "incident-1", order_id, task_queue="paper-recovery-test"
                )

    asyncio.run(run())


def test_temporal_failed_containment_cannot_reach_review(tmp_path):
    binary = temporal_binary()

    async def run():
        from ai_quant_os.temporal_recovery import (
            PaperRecoveryWorkflow,
            RecoveryActivities,
            start_paper_recovery,
        )
        from ai_quant_os.risk import SafetyState

        service, execution, risk, state, broker = recovery_fixture(tmp_path)
        order_id = execution.orders[0].order_id
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=binary,
            dev_server_log_level="error",
            ui=False,
        ) as environment:
            activities = RecoveryActivities(service)
            with patch.object(state, "record_event", side_effect=OSError("injected disk failure")):
                async with Worker(
                    environment.client,
                    task_queue="failed-recovery-test",
                    workflows=[PaperRecoveryWorkflow],
                    activities=activities.registered(),
                ):
                    handle = await start_paper_recovery(
                        environment.client, "failed-1", order_id, task_queue="failed-recovery-test"
                    )
                    result = await asyncio.wait_for(handle.result(), 20)
            assert result["stage"] == "FAILED"
            assert result["promotion_allowed"] is False
            assert risk.safety_state == SafetyState.HALT
            assert execution.orders[0].status == "UNKNOWN"
            assert len(broker.orders) == 1

    asyncio.run(run())


def test_temporal_retries_are_bounded_without_releasing_reserved_exposure(tmp_path):
    binary = temporal_binary()

    async def run():
        from ai_quant_os.temporal_recovery import (
            PaperRecoveryWorkflow,
            RecoveryActivities,
            start_paper_recovery,
        )
        from ai_quant_os.risk import SafetyState
        from decimal import Decimal

        service, execution, risk, state, broker = recovery_fixture(tmp_path)
        order_id = execution.orders[0].order_id
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=binary,
            dev_server_log_level="error",
            ui=False,
        ) as environment:
            activities = RecoveryActivities(service)
            with patch.object(broker, "lookup", return_value=None):
                async with Worker(
                    environment.client,
                    task_queue="bounded-recovery-test",
                    workflows=[PaperRecoveryWorkflow],
                    activities=activities.registered(),
                ):
                    handle = await start_paper_recovery(
                        environment.client,
                        "bounded-1",
                        order_id,
                        task_queue="bounded-recovery-test",
                    )
                    for attempt in range(1, 21):
                        async with asyncio.timeout(20):
                            while (
                                state.audit_event(f"paper-recovery:bounded-1:attempt:{attempt}")
                                is None
                            ):
                                await asyncio.sleep(0.05)
                        if attempt < 20:
                            await wait_stage(handle, "WAITING_RETRY")
                            await handle.signal("retry")
                    result = await asyncio.wait_for(handle.result(), 20)
            assert result["stage"] == "UNRESOLVED"
            assert result["attempt"] == 20
            assert result["promotion_allowed"] is False
            assert state.audit_event("paper-recovery:bounded-1:attempt:21") is None
            assert risk.safety_state == state.safety_state() == SafetyState.NO_NEW_RISK
            assert execution.exposure_for("EURUSD").pending_buy_quantity == Decimal(100)
            assert len(broker.orders) == 1

    asyncio.run(run())

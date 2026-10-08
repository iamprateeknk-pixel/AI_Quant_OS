"""Temporal retains dispatch history; cancellation drains an owned prefix."""

import asyncio
import importlib
import json
import subprocess
import sys
from threading import Event

import pytest

pytest.importorskip("temporalio")
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_quant_os.candidate_validation_store import CandidateValidationStore
from test_candidate_validation_job import prepared, controller
from test_candidate_dataset_views import unattested_test_metadata
from test_candidate_validation_store import failed_tests
from test_strategy_prefix_worker import package as package
from test_strategy_prefix_run import scripted as scripted
from test_strategy_image_loading import assembled as assembled, inputs as inputs, daemon as daemon
from test_temporal_recovery import temporal_binary


def temporal():
    assert importlib.util.find_spec("ai_quant_os.temporal_validation"), (
        "Durable Temporal validation workflow required"
    )
    return importlib.import_module("ai_quant_os.temporal_validation")


def test_queued_validation_survives_temporal_server_restart_and_refuses_duplicate_start(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    observed = failed_tests(package)
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: observed)
    monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *a: observed)
    module = temporal()

    async def run():
        arguments = dict(
            dev_server_existing_path=temporal_binary(),
            dev_server_database_filename=str(tmp_path / "temporal.sqlite3"),
            dev_server_log_level="error",
            ui=False,
        )
        activity_args = dict(
            root=root, job_sha256=manifest.job_sha256, archive_path=tmp_path / "results.sqlite3"
        )
        activities = module.ValidationActivities(package, **activity_args)
        async with await WorkflowEnvironment.start_local(**arguments) as environment:
            await module.start_validation_workflow(
                environment.client, activities, run_id="restart-1", task_queue="validation-test"
            )
            with pytest.raises(WorkflowAlreadyStartedError):
                await module.start_validation_workflow(
                    environment.client, activities, run_id="restart-1", task_queue="validation-test"
                )
        activities = module.ValidationActivities(package, **activity_args)
        async with await WorkflowEnvironment.start_local(**arguments) as environment:
            handle = environment.client.get_workflow_handle(
                "candidate-validation:" + manifest.job_sha256 + ":restart-1"
            )
            async with Worker(
                environment.client,
                task_queue="validation-test",
                workflows=[module.CandidateValidationWorkflow],
                activities=activities.registered(),
            ):
                result = await asyncio.wait_for(handle.result(), 30)
            assert result["state"] == "ADVISORY_RESULT_RETAINED"
            assert result["artifact_ref"].startswith("candidate-prerequisite-failure:")
            assert result["promotion_allowed"] is False
            history = await handle.fetch_history()
            scheduled = [
                event
                for event in history.events
                if event.HasField("activity_task_scheduled_event_attributes")
            ]
            assert len(scheduled) == 2
            assert all(
                event.activity_task_scheduled_event_attributes.retry_policy.maximum_attempts == 1
                for event in scheduled
            )
        with CandidateValidationStore(tmp_path / "results.sqlite3") as archive:
            assert archive.get(result["artifact_ref"]).test_result == observed

    asyncio.run(run())
    assert not scripted[2]


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {"phase": "COMPLETED"},
        {"index": 0, "stage": "TESTS", "phase": "COMPLETED"},
        {
            "index": 0,
            "stage": "PUBLICATION",
            "phase": "COMPLETED",
            "manifest_sha256": "0" * 64,
            "result_sha256": "0" * 64,
            "artifact_ref": "candidate-validation:" + "0" * 64,
        },
    ],
)
def test_malformed_activity_record_stops_once_without_inventing_a_result(
    package, scripted, tmp_path, malformed
):
    root, manifest = prepared(package, scripted, tmp_path)
    module = temporal()

    @module.activity.defn(name="candidate_validation_stage")
    async def invalid(request: module.ValidationWorkflowRequest):
        if malformed is None:
            return None
        return {"job_sha256": request.job_sha256, "promotion_allowed": False, **malformed}

    async def run():
        activities = module.ValidationActivities(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            archive_path=tmp_path / "results.sqlite3",
        )
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=temporal_binary(), dev_server_log_level="error", ui=False
        ) as environment:
            async with Worker(
                environment.client,
                task_queue="invalid-record",
                workflows=[module.CandidateValidationWorkflow],
                activities=[invalid],
            ):
                handle = await module.start_validation_workflow(
                    environment.client,
                    activities,
                    run_id="invalid-record",
                    task_queue="invalid-record",
                )
                result = await asyncio.wait_for(handle.result(), 10)
            assert (
                result["state"] == "WAIT_FOR_PINNED_RECONCILIATION"
                and result["artifact_ref"] is None
            )
            history = await handle.fetch_history()
            assert (
                sum(
                    event.HasField("activity_task_scheduled_event_attributes")
                    for event in history.events
                )
                == 1
            )

    asyncio.run(run())
    assert not scripted[2]


def test_start_refuses_uncertain_job_before_temporal_dispatch(
    package, scripted, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    root, manifest = prepared(package, scripted, tmp_path)

    def uncertain(*args):
        raise RuntimeError("uncertain")

    monkeypatch.setattr(controller(), "run_candidate_tests", uncertain)
    with CandidateValidationStore() as archive, pytest.raises(RuntimeError):
        controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )
    module = temporal()
    activities = module.ValidationActivities(
        package,
        root=root,
        job_sha256=manifest.job_sha256,
        archive_path=tmp_path / "results.sqlite3",
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("Uncertain job must refuse before Temporal dispatch")

    with pytest.raises(ValueError):
        asyncio.run(
            module.start_validation_workflow(
                SimpleNamespace(start_workflow=forbidden),
                activities,
                run_id="unsafe-repeat",
                task_queue="unsafe-repeat",
            )
        )


def test_uncertain_temporal_stage_has_no_automatic_worker_retry(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    dispatches = []

    def uncertain(*args):
        dispatches.append(True)
        raise RuntimeError("private uncertain worker outcome")

    monkeypatch.setattr(controller(), "run_candidate_tests", uncertain)
    module = temporal()

    async def run():
        activities = module.ValidationActivities(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            archive_path=tmp_path / "results.sqlite3",
        )
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=temporal_binary(), dev_server_log_level="error", ui=False
        ) as environment:
            async with Worker(
                environment.client,
                task_queue="uncertain-validation",
                workflows=[module.CandidateValidationWorkflow],
                activities=activities.registered(),
            ):
                handle = await module.start_validation_workflow(
                    environment.client,
                    activities,
                    run_id="uncertain-1",
                    task_queue="uncertain-validation",
                )
                result = await asyncio.wait_for(handle.result(), 30)
            assert result["state"] == "WAIT_FOR_PINNED_RECONCILIATION"
            assert result["artifact_ref"] is None and "private" not in str(result)
        assert (
            controller()
            .validation_job_status(package, root=root, job_sha256=manifest.job_sha256)
            .phase
            == "UNCERTAIN"
        )

    asyncio.run(run())
    assert dispatches == [True]


def test_cancelled_validation_activity_drains_owned_prefix_before_releasing_root(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    observed = unattested_test_metadata(package)
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: observed)
    monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *a: observed)
    with CandidateValidationStore(tmp_path / "results.sqlite3") as archive:
        controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )
    module = temporal()
    import ai_quant_os.strategy_container_lifecycle as containers

    command = containers.local_docker_command
    entered, release = Event(), Event()

    def blocked_start(executable, *args, **kwargs):
        result = command(executable, *args, **kwargs)
        if args[:2] == ("container", "start"):
            entered.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(containers, "local_docker_command", blocked_start)
    # This unit probe supplies the SDK context; server/history behavior is tested above.
    monkeypatch.setattr(module.activity, "heartbeat", lambda *a: None)

    async def run():
        activities = module.ValidationActivities(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            archive_path=tmp_path / "results.sqlite3",
        )
        task = asyncio.create_task(activities.step(activities.request))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(0.05)
            assert not task.done()
            task.cancel()
            await asyncio.sleep(0.05)
            assert not task.done()
            with pytest.raises(ValueError):
                controller().validation_job_status(
                    package, root=root, job_sha256=manifest.job_sha256
                )
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not scripted[3]
        paused = controller().validation_job_status(
            package, root=root, job_sha256=manifest.job_sha256
        )
        assert paused.phase == "PAUSED"
        assert len([call for call in scripted[2] if call[:2] == ("container", "start")]) == 1

    asyncio.run(run())


def test_activity_refuses_foreign_job_binding_before_dispatch(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    module = temporal()
    activities = module.ValidationActivities(
        package,
        root=root,
        job_sha256=manifest.job_sha256,
        archive_path=tmp_path / "results.sqlite3",
    )
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: pytest.fail("No dispatch"))
    with pytest.raises(ApplicationError):
        asyncio.run(
            activities.step(module.ValidationWorkflowRequest("0" * 64, manifest.stage_count))
        )


def test_workflow_cli_returns_retained_result_with_real_local_temporal_server(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    observed = failed_tests(package)
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: observed)
    monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *a: observed)
    archive_path = tmp_path / "results.sqlite3"
    with CandidateValidationStore(archive_path) as archive:
        controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )
        final = controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )

    async def run():
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=temporal_binary(), dev_server_log_level="error", ui=False
        ) as environment:
            command = [
                sys.executable,
                "-m",
                "ai_quant_os.candidate_validation_job_cli",
                "--validation-archive",
                str(tmp_path / "paper.validation-packages.sqlite3"),
                "--package-ref",
                package.package_ref,
                "--root",
                str(root),
                "workflow",
                "--job-sha256",
                manifest.job_sha256,
                "--record-archive",
                str(archive_path),
                "--run-id",
                "cli-final",
                "--temporal-endpoint",
                environment.client.service_client.config.target_host,
            ]
            child = await asyncio.to_thread(
                subprocess.run, command, capture_output=True, text=True, timeout=30
            )
            assert child.returncode == 0, child.stderr
            result = json.loads(child.stdout)
            assert result["state"] == "ADVISORY_RESULT_RETAINED"
            assert (
                result["artifact_ref"] == final.artifact_ref
                and result["promotion_allowed"] is False
            )

    asyncio.run(run())
    assert not scripted[2]

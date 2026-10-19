"""Durable repair orchestration remains contained and never promotes a candidate."""

import asyncio
import importlib
import os
from pathlib import Path
import subprocess

import pytest

pytest.importorskip("temporalio")
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from test_health_watchdog import worker as health_worker


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "production"
    root.mkdir()

    def git(*arguments):
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Temporal repair fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "core.autocrlf", "false")
    source = root / "src" / "ai_quant_os"
    source.mkdir(parents=True)
    (source / "regime_agent.py").write_text('VERSION = "0.2.0"\n', newline="\n")
    (source / "risk.py").write_text("LIMIT = 100\n", newline="\n")
    git("add", ".")
    git("commit", "-m", "Baseline")
    return root


def temporal():
    assert importlib.util.find_spec("ai_quant_os.temporal_repair"), (
        "Durable Temporal repair workflow required"
    )
    return importlib.import_module("ai_quant_os.temporal_repair")


def temporal_binary():
    path = Path(
        os.environ.get("AI_QUANT_TEMPORAL_CLI", "data-local/tools/temporal-1.9.1/temporal.exe")
    )
    if not path.is_file():
        if os.environ.get("AI_QUANT_REQUIRE_TEMPORAL") == "1":
            pytest.fail("Pinned Temporal CLI is required by this verification gate")
        pytest.skip("Install the pinned Temporal CLI for actual service tests")
    return str(path.resolve())


def test_repair_workflow_resumes_a_checkpoint_after_temporal_server_restart(tmp_path):
    module = temporal()
    request = module.RepairRecoveryRequest("a" * 64, "b" * 64, "c" * 32)
    database = str(tmp_path / "temporal.sqlite3")
    arguments = {
        "dev_server_existing_path": temporal_binary(),
        "dev_server_database_filename": database,
        "dev_server_log_level": "error",
        "ui": False,
    }
    observed = {"recorded": False, "calls": 0}

    @module.activity.defn(name="repair_recovery_contain")
    async def contain(value: module.RepairRecoveryRequest):
        assert value == request
        return {
            "incident_id": value.incident_id,
            "safety_state": "HALT",
            "audit_valid": True,
            "promotion_allowed": False,
        }

    @module.activity.defn(name="repair_execution_checkpoint")
    async def checkpoint(value: module.RepairRecoveryRequest):
        observed["calls"] += 1
        return {
            "incident_id": value.incident_id,
            "candidate_id": value.candidate_id,
            "owner": value.execution_owner,
            "snapshot_sha256": "d" * 64,
            "state": (
                "EXECUTION_RECORDED" if observed["recorded"] else "EXECUTION_OUTCOME_UNCONFIRMED"
            ),
            "execution_state": (
                "EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION"
                if observed["recorded"]
                else None
            ),
            "safety_state": "HALT",
            "candidate_bound": True,
            "promotion_allowed": False,
        }

    @module.activity.defn(name="repair_verification_certificate")
    async def verify_certificate(value):
        assert value == [request.__dict__, "e" * 64]
        return {
            "incident_id": request.incident_id,
            "candidate_id": request.candidate_id,
            "snapshot_sha256": "d" * 64,
            "certificate_sha256": "e" * 64,
            "state": "INDEPENDENT_VERIFICATION_PASSED",
            "promotion_allowed": False,
        }

    async def wait_stage(handle, expected):
        async with asyncio.timeout(20):
            while await handle.query("stage") != expected:
                await asyncio.sleep(0.05)

    async def run():
        async with await WorkflowEnvironment.start_local(**arguments) as environment:
            async with Worker(
                environment.client,
                task_queue="repair-resume",
                workflows=[module.RepairRecoveryWorkflow],
                activities=[contain, checkpoint],
            ):
                handle = await module.start_repair_recovery(
                    environment.client,
                    request,
                    run_id="restart-1",
                    task_queue="repair-resume",
                )
                await wait_stage(handle, "WAIT_FOR_PINNED_RECONCILIATION")
                workflow_id = handle.id

        observed["recorded"] = True
        async with await WorkflowEnvironment.start_local(**arguments) as environment:
            handle = environment.client.get_workflow_handle(workflow_id)
            async with Worker(
                environment.client,
                task_queue="repair-resume",
                workflows=[module.RepairRecoveryWorkflow],
                activities=[contain, checkpoint, verify_certificate],
            ):
                await handle.signal("reconcile")
                await wait_stage(handle, "AWAITING_INDEPENDENT_VERIFICATION")
                await handle.signal("verification_certificate", "e" * 64)
                result = await asyncio.wait_for(handle.result(), 30)
            assert result["stage"] == "INDEPENDENT_VERIFICATION_PASSED"
            assert result["safety_state"] == "HALT"
            assert result["promotion_allowed"] is False
            assert result["production_released"] is False
            history = await handle.fetch_history()
            scheduled = [
                event
                for event in history.events
                if event.HasField("activity_task_scheduled_event_attributes")
            ]
            assert len(scheduled) == 4
            assert all(
                event.activity_task_scheduled_event_attributes.retry_policy.maximum_attempts == 1
                for event in scheduled
            )

    asyncio.run(run())
    assert observed["calls"] == 2


def test_failed_repair_cannot_be_promoted_by_temporal_workflow(tmp_path):
    module = temporal()
    request = module.RepairRecoveryRequest("a" * 64, "b" * 64, "c" * 32)

    @module.activity.defn(name="repair_recovery_contain")
    async def contain(value: module.RepairRecoveryRequest):
        return {
            "incident_id": value.incident_id,
            "safety_state": "NO_NEW_RISK",
            "audit_valid": True,
            "promotion_allowed": False,
        }

    @module.activity.defn(name="repair_execution_checkpoint")
    async def checkpoint(value: module.RepairRecoveryRequest):
        return {
            "incident_id": value.incident_id,
            "candidate_id": value.candidate_id,
            "owner": value.execution_owner,
            "snapshot_sha256": "d" * 64,
            "state": "EXECUTION_RECORDED",
            "execution_state": "EXECUTION_FAILED_AWAITING_REVIEW",
            "safety_state": "NO_NEW_RISK",
            "candidate_bound": True,
            "promotion_allowed": False,
        }

    async def run():
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=temporal_binary(),
            dev_server_log_level="error",
            ui=False,
        ) as environment:
            async with Worker(
                environment.client,
                task_queue="repair-failed",
                workflows=[module.RepairRecoveryWorkflow],
                activities=[contain, checkpoint],
            ):
                handle = await module.start_repair_recovery(
                    environment.client,
                    request,
                    run_id="failed-1",
                    task_queue="repair-failed",
                )
                result = await asyncio.wait_for(handle.result(), 30)
        assert result["stage"] == "REPAIR_FAILED_AWAITING_REVIEW"
        assert result["safety_state"] == "NO_NEW_RISK"
        assert result["promotion_allowed"] is False
        assert result["production_released"] is False

    asyncio.run(run())


def test_malformed_temporal_activity_enums_fail_closed():
    from ai_quant_os.temporal_repair import RepairRecoveryRequest, RepairRecoveryWorkflow

    request = RepairRecoveryRequest("a" * 64, "b" * 64, "c" * 32)
    malformed_containment = {
        "incident_id": request.incident_id,
        "safety_state": [],
        "audit_valid": True,
        "promotion_allowed": False,
    }
    malformed_checkpoint = {
        "incident_id": request.incident_id,
        "candidate_id": request.candidate_id,
        "owner": request.execution_owner,
        "snapshot_sha256": "d" * 64,
        "state": "EXECUTION_RECORDED",
        "execution_state": [],
        "safety_state": "HALT",
        "candidate_bound": True,
        "promotion_allowed": False,
    }

    assert RepairRecoveryWorkflow._valid_containment(malformed_containment, request) is False
    assert RepairRecoveryWorkflow._valid_checkpoint(malformed_checkpoint, request, "HALT") is False


def test_critical_health_incident_is_durably_contained_before_repair_review(tmp_path):
    from datetime import datetime, timezone

    from ai_quant_os.health_contracts import HealthIncident
    from ai_quant_os.temporal_repair import RepairRecoveryActivities, RepairRecoveryRequest
    from ai_quant_os.risk import SafetyState

    watchdog, store, state, risk, _ = health_worker(tmp_path)
    incident = store.record(
        HealthIncident(
            "GRAPH_INTEGRITY_FAILED",
            "graph-ledger",
            '{"integrity":"failed"}',
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    request = RepairRecoveryRequest(incident.incident_id, "a" * 64, "b" * 32)
    activity = RepairRecoveryActivities(watchdog, object(), jobs_root=tmp_path / "jobs")

    result = activity._contain(request)

    assert result["safety_state"] == "HALT"
    assert result["promotion_allowed"] is False
    assert risk.safety_state == state.safety_state() == SafetyState.HALT


def test_execution_checkpoint_binds_owner_receipt_to_exact_incident_candidate(
    tmp_path, repository, monkeypatch
):
    from types import SimpleNamespace

    from test_repair_workspace import prepare, workspace as make_workspace
    from ai_quant_os.repair_containers import RepairExecutionReceipt, RepairExecutionRecovery
    from ai_quant_os.risk import SafetyState
    from ai_quant_os.temporal_repair import RepairRecoveryActivities, RepairRecoveryRequest

    repair_workspace, incident, _ = make_workspace(tmp_path, repository)
    candidate = prepare(repair_workspace, incident)
    snapshot = repair_workspace.snapshot(candidate["candidate_id"])
    owner = "b" * 32
    receipt = RepairExecutionReceipt(
        schema_version="1.0",
        owner=owner,
        container_id="c" * 64,
        image_id="sha256:" + "d" * 64,
        snapshot_sha256=snapshot.snapshot_sha256,
        suite_sha256="e" * 64,
        request_sha256="f" * 64,
        state="EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION",
        exit_code=0,
        oom_killed=False,
        stdout_bytes=0,
        stdout_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        stderr_bytes=0,
        stderr_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    monkeypatch.setattr(
        "ai_quant_os.repair_containers.read_repair_execution",
        lambda root, value: RepairExecutionRecovery(
            schema_version="1.0",
            owner=value,
            state="EXECUTION_RECORDED",
            retained_artifacts=("result.json", "start-intent.json", "stderr", "stdout"),
            receipt=receipt,
        ),
    )
    watchdog = SimpleNamespace(state=SimpleNamespace(safety_state=lambda: SafetyState.NO_NEW_RISK))
    activity = RepairRecoveryActivities(
        watchdog, repair_workspace, jobs_root=tmp_path / "repairs" / "jobs"
    )
    request = RepairRecoveryRequest(incident.incident_id, candidate["candidate_id"], owner)

    result = activity._checkpoint(request)

    assert result["candidate_bound"] is True
    assert result["snapshot_sha256"] == snapshot.snapshot_sha256
    assert result["promotion_allowed"] is False
    with pytest.raises(ValueError, match="not bound"):
        activity._checkpoint(RepairRecoveryRequest("0" * 64, candidate["candidate_id"], owner))

"""Optional Temporal dispatch of one locally admitted original-source job."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import re
from threading import Event

from temporalio import activity, workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from ai_quant_os.candidate_validation_job_contracts import (
        ValidationStageRecord,
        validation_stage_name,
    )


@dataclass(frozen=True)
class ValidationWorkflowRequest:
    job_sha256: str
    stage_count: int

    def __post_init__(self):
        if (
            type(self.job_sha256) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", self.job_sha256)
            or type(self.stage_count) is not int
            or not 4 <= self.stage_count <= 2051
        ):
            raise ValueError("Exact bounded validation workflow declaration required")


@workflow.defn
class CandidateValidationWorkflow:
    def __init__(self):
        self._stage = "QUEUED"

    @workflow.query(name="stage")
    def stage(self) -> str:
        return self._stage

    @workflow.run
    async def run(self, request: ValidationWorkflowRequest) -> dict:
        result = {
            "state": "WAIT_FOR_PINNED_RECONCILIATION",
            "artifact_ref": None,
            "promotion_allowed": False,
        }
        for _ in range(request.stage_count):
            self._stage = "DISPATCHING_STAGE"
            try:
                record = await workflow.execute_activity(
                    "candidate_validation_stage",
                    request,
                    start_to_close_timeout=timedelta(days=7),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
            except ActivityError:
                self._stage = result["state"]
                return result
            if type(record) is not dict or record.get("job_sha256") != request.job_sha256:
                self._stage = result["state"]
                return result
            try:
                observed = ValidationStageRecord.model_validate(
                    {key: value for key, value in record.items() if key != "job_sha256"}
                )
                if observed.stage != validation_stage_name(observed.index, request.stage_count):
                    raise ValueError("Stage differs from original declaration")
            except (ValueError, TypeError):
                self._stage = result["state"]
                return result
            if observed.phase == "PAUSED":
                self._stage = "PAUSED_RESTART_REQUIRES_NEW_RUN_ID"
                return {**result, "state": self._stage}
            if observed.phase != "COMPLETED":
                self._stage = result["state"]
                return result
            self._stage = observed.stage
            if self._stage == "PUBLICATION":
                reference = observed.artifact_ref
                if type(reference) is not str or not re.fullmatch(
                    r"candidate-(validation|prerequisite-failure):[a-f0-9]{64}", reference
                ):
                    self._stage = result["state"]
                    return result
                self._stage = "ADVISORY_RESULT_RETAINED"
                return {**result, "state": self._stage, "artifact_ref": reference}
        self._stage = result["state"]
        return result


class ValidationActivities:
    def __init__(self, package, *, root: Path, job_sha256: str, archive_path: Path):
        from ai_quant_os.candidate_validation_job import read_validation_job_manifest

        self._package, self._root = package, Path(root).absolute()
        self._archive_path = Path(archive_path).absolute()
        manifest = read_validation_job_manifest(package, root=self._root, job_sha256=job_sha256)
        self.request = ValidationWorkflowRequest(job_sha256, manifest.stage_count)

    def _require(self, request):
        if type(request) is not ValidationWorkflowRequest or request != self.request:
            raise ApplicationError("Validation activity binding refused", non_retryable=True)

    def _step(self, stopped):
        from ai_quant_os.candidate_validation_job import step_validation_job, ValidationJobPaused
        from ai_quant_os.candidate_validation_store import CandidateValidationStore

        def checkpoint():
            if stopped.is_set():
                raise ValidationJobPaused("Cooperative activity stop requested")

        with CandidateValidationStore(self._archive_path) as archive:
            record = step_validation_job(
                self._package,
                root=self._root,
                job_sha256=self.request.job_sha256,
                archive=archive,
                checkpoint=checkpoint,
            )
        return {"job_sha256": self.request.job_sha256, **record.model_dump(mode="json")}

    async def _call(self):
        stopped = Event()
        task = asyncio.create_task(asyncio.to_thread(self._step, stopped))
        try:
            while not task.done():
                activity.heartbeat(self.request.job_sha256)
                await asyncio.wait({task}, timeout=5)
        except BaseException as interruption:
            stopped.set()
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise
                except Exception as error:
                    raise interruption from error
            raise
        return await task

    @activity.defn(name="candidate_validation_stage")
    async def step(self, request: ValidationWorkflowRequest) -> dict:
        self._require(request)
        try:
            return await self._call()
        except Exception as error:
            raise ApplicationError(
                "Validation stage stopped; retain original custody and pins",
                type=type(error).__name__,
                non_retryable=True,
            ) from error

    def registered(self):
        return [self.step]


async def start_validation_workflow(
    client, activities: ValidationActivities, *, run_id: str, task_queue: str
):
    """Trusted local operator entry; job paths and inputs belong to the worker binding."""
    from ai_quant_os.candidate_validation_job import validation_job_status

    if (
        type(activities) is not ValidationActivities
        or type(run_id) is not str
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id)
        or type(task_queue) is not str
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_queue)
    ):
        raise ValueError("Exact local validation workflow identity required")
    status = await asyncio.to_thread(
        validation_job_status,
        activities._package,
        root=activities._root,
        job_sha256=activities.request.job_sha256,
    )
    if status is not None and status.phase in {"RESERVED", "PREPARED", "UNCERTAIN"}:
        raise ValueError("Pinned reconciliation required before workflow dispatch")
    return await client.start_workflow(
        CandidateValidationWorkflow.run,
        activities.request,
        id="candidate-validation:" + activities.request.job_sha256 + ":" + run_id,
        task_queue=task_queue,
        execution_timeout=timedelta(days=14),
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        rpc_timeout=timedelta(seconds=10),
    )

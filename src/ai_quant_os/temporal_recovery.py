"""Optional Temporal boundary for the paper broker uncertainty runbook."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError

if TYPE_CHECKING:
    from ai_quant_os.paper_recovery import PaperRecoveryService


@dataclass(frozen=True)
class RecoveryRequest:
    incident_id: str
    order_id: str


@workflow.defn
class PaperRecoveryWorkflow:
    def __init__(self) -> None:
        self._stage = "CONTAINING"
        self._retry = False

    @workflow.query(name="stage")
    def stage(self) -> str:
        return self._stage

    @workflow.signal(name="retry")
    def retry(self) -> None:
        # A signal requests a fresh lookup; it supplies no purported verification.
        # Bursts coalesce rather than queueing unbounded work.
        self._retry = True

    @workflow.run
    async def run(self, request: RecoveryRequest) -> dict:
        options = {
            "start_to_close_timeout": timedelta(seconds=30),
            "retry_policy": RetryPolicy(maximum_attempts=3),
        }
        result = {
            "incident_id": request.incident_id,
            "order_id": request.order_id,
            "stage": "FAILED",
            "promotion_allowed": False,
        }
        try:
            await workflow.execute_activity("paper_recovery_contain", request, **options)
            deadline = workflow.now() + timedelta(days=7)
            for attempt in range(1, 21):
                self._stage = "RECONCILING"
                self._retry = False
                result = await workflow.execute_activity(
                    "paper_recovery_reconcile",
                    args=[request, attempt],
                    **options,
                )
                self._stage = result["stage"]
                if self._stage == "REVIEW_REQUIRED":
                    return result
                remaining = deadline - workflow.now()
                if attempt == 20 or remaining <= timedelta(0):
                    break
                try:
                    await workflow.wait_condition(lambda: self._retry, timeout=remaining)
                except TimeoutError:
                    break
            self._stage = "UNRESOLVED"
            return {**result, "stage": self._stage}
        except ActivityError:
            self._stage = "FAILED"
            return {**result, "stage": self._stage, "promotion_allowed": False}


class RecoveryActivities:
    def __init__(self, service: "PaperRecoveryService") -> None:
        self._service = service

    async def _call(self, method, *args):
        try:
            task = asyncio.create_task(asyncio.to_thread(method, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Cancellation cannot interrupt SQLite/thread work. Drain before
                # releasing activity ownership or closing the application lifespan.
                while True:
                    try:
                        await asyncio.shield(task)
                        break
                    except asyncio.CancelledError:
                        if task.cancelled():
                            # Forced event-loop teardown can cancel every task.
                            # It must not turn draining into a busy loop.
                            raise
                        continue
                raise
        except (ValueError, OSError) as error:
            raise ApplicationError(
                "Paper recovery failed closed; inspect the owning ledger",
                type=type(error).__name__,
                non_retryable=True,
            ) from error

    @activity.defn(name="paper_recovery_contain")
    async def contain(self, request: RecoveryRequest) -> dict:
        return await self._call(self._service.contain, request.incident_id, request.order_id)

    @activity.defn(name="paper_recovery_reconcile")
    async def reconcile(self, request: RecoveryRequest, attempt: int) -> dict:
        return await self._call(
            self._service.reconcile,
            request.incident_id,
            request.order_id,
            attempt,
        )

    def registered(self):
        return [self.contain, self.reconcile]


async def start_paper_recovery(
    client: Client,
    incident_id: str,
    order_id: str,
    *,
    task_queue: str,
):
    """Trusted operator boundary; external transports must authenticate separately."""
    from ai_quant_os.paper_recovery import validate_recovery_ids

    validate_recovery_ids(incident_id, order_id)
    return await client.start_workflow(
        PaperRecoveryWorkflow.run,
        RecoveryRequest(incident_id, order_id),
        id=f"paper-recovery:{incident_id}",
        task_queue=task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )

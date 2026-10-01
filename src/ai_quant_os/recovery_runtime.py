"""Local-only Temporal composition with the application's actual risk owner."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from hashlib import sha256
from os.path import normcase
from uuid import uuid4

from fastapi import HTTPException
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

from ai_quant_os.paper_recovery import PaperRecoveryService, validate_recovery_ids
from ai_quant_os.risk import SafetyState
from ai_quant_os.temporal_recovery import PaperRecoveryWorkflow, RecoveryActivities, RecoveryRequest


class RecoveryRuntime:
    def __init__(self, client, service, state, owner):
        self.client = client
        self.service = service
        self.state = state
        self.owner = owner
        self.queue = f"quant-paper-recovery-{owner}"

    @classmethod
    @asynccontextmanager
    async def open(cls, target, path, execution, risk, state, control_lock):
        owner = sha256(normcase(str(path.resolve())).encode()).hexdigest()[:32]
        service = PaperRecoveryService(execution, risk, state, control_lock=control_lock)
        try:
            client = await asyncio.wait_for(Client.connect(target), 5)
            runtime = cls(client, service, state, owner)
            activities = RecoveryActivities(service)
            async with Worker(
                client,
                task_queue=runtime.queue,
                workflows=[PaperRecoveryWorkflow],
                activities=activities.registered(),
                graceful_shutdown_timeout=timedelta(seconds=30),
            ):
                yield runtime
        except BaseException:

            def stop():
                with control_lock:
                    key = f"temporal-worker-failure:{uuid4()}"
                    try:
                        risk.set_safety_state(
                            SafetyState.HALT,
                            before_change=lambda: state.record_control(
                                key,
                                SafetyState.HALT,
                                "PAPER_RECOVERY_WORKER_FAILED",
                                {"receipt_id": key, "safety_state": "HALT"},
                            ),
                        )
                    finally:
                        risk.set_safety_state(SafetyState.HALT)

            await asyncio.to_thread(stop)
            raise

    def _handle(self, incident_id):
        try:
            validate_recovery_ids(incident_id, "validation")
        except ValueError as error:
            raise HTTPException(422, "Invalid recovery identifier") from error
        return self.client.get_workflow_handle(f"paper-recovery:{self.owner}:{incident_id}")

    async def _describe(self, handle):
        try:
            return await handle.describe(rpc_timeout=timedelta(seconds=3))
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, "Unknown paper recovery incident") from error
            raise HTTPException(503, "Paper recovery service unavailable") from error

    async def start(self, incident_id, order_id):
        handle = self._handle(incident_id)
        try:
            await self._describe(handle)
        except HTTPException as error:
            if error.status_code != 404:
                raise
        else:
            raise HTTPException(409, "Recovery incident already exists")
        try:
            await asyncio.to_thread(self.service.contain, incident_id, order_id)
        except ValueError as error:
            raise HTTPException(
                404, "Paper order unavailable or incident ownership conflict"
            ) from error
        try:
            await self.client.start_workflow(
                PaperRecoveryWorkflow.run,
                RecoveryRequest(incident_id, order_id),
                id=handle.id,
                task_queue=self.queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                rpc_timeout=timedelta(seconds=3),
            )
        except WorkflowAlreadyStartedError as error:
            raise HTTPException(409, "Recovery incident already exists") from error
        except RPCError as error:
            raise HTTPException(
                503, "Recovery start unconfirmed; paper containment retained"
            ) from error
        return {
            "incident_id": incident_id,
            "workflow_id": handle.id,
            "state": "SUBMITTED",
            "promotion_allowed": False,
        }

    async def view(self, incident_id):
        handle = self._handle(incident_id)
        admission = await asyncio.to_thread(
            self.state.audit_event, f"paper-recovery:{incident_id}:contained"
        )
        if admission is None:
            raise HTTPException(404, "Unknown paper recovery incident")
        description = await self._describe(handle)
        result = None
        stage = None
        try:
            if description.status == WorkflowExecutionStatus.COMPLETED:
                result = await asyncio.wait_for(handle.result(), 3)
            elif description.status == WorkflowExecutionStatus.RUNNING:
                stage = await handle.query("stage", rpc_timeout=timedelta(seconds=3))
        except (RPCError, TimeoutError) as error:
            raise HTTPException(503, "Paper recovery service unavailable") from error
        return {
            "incident_id": incident_id,
            "order_id": admission["order_id"],
            "state": description.status.name,
            "stage": stage,
            "result": result,
            "promotion_allowed": False,
        }

    async def retry(self, incident_id):
        view = await self.view(incident_id)
        if view["state"] != "RUNNING" or view["stage"] != "WAITING_RETRY":
            raise HTTPException(409, "Recovery incident is not waiting for retry")
        try:
            await self._handle(incident_id).signal("retry", rpc_timeout=timedelta(seconds=3))
        except RPCError as error:
            raise HTTPException(503, "Recovery retry unconfirmed") from error
        return {"incident_id": incident_id, "state": "RETRY_REQUESTED", "promotion_allowed": False}

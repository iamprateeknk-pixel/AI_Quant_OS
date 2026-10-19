"""Local-only Temporal composition with the application's actual risk owner."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from hashlib import sha256
from os.path import normcase
from pathlib import Path
import re
import os
import stat
import tempfile
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


def _persist_repair_artifact(path, raw, *, limit):
    from ai_quant_os.repair_image_inputs import read_repair_image_input

    if type(raw) is not bytes or not 1 <= len(raw) <= limit:
        raise HTTPException(413, "Repair evidence exceeds its size limit")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".repair-evidence-", delete=False
        ) as output:
            temporary_path = output.name
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            try:
                retained = read_repair_image_input(path, limit)
            except (OSError, ValueError) as error:
                raise HTTPException(409, "Retained repair evidence is invalid") from error
            if retained != raw:
                raise HTTPException(409, "Repair evidence identity conflict")
    finally:
        if temporary_path is not None:
            Path(temporary_path).unlink(missing_ok=True)


class RecoveryRuntime:
    def __init__(self, client, service, state, owner, *, repair_activities=None):
        self.client = client
        self.service = service
        self.state = state
        self.owner = owner
        self.repair_activities = repair_activities
        self.queue = f"quant-paper-recovery-{owner}"

    @classmethod
    @asynccontextmanager
    async def open(
        cls, target, path, execution, risk, state, control_lock, *, repair_activities=None
    ):
        owner = sha256(normcase(str(path.resolve())).encode()).hexdigest()[:32]
        service = PaperRecoveryService(execution, risk, state, control_lock=control_lock)
        try:
            client = await asyncio.wait_for(Client.connect(target), 5)
            runtime = cls(client, service, state, owner, repair_activities=repair_activities)
            activities = RecoveryActivities(service)
            workflows = [PaperRecoveryWorkflow]
            registered_activities = activities.registered()
            if repair_activities is not None:
                from ai_quant_os.temporal_repair import RepairRecoveryWorkflow

                workflows.append(RepairRecoveryWorkflow)
                registered_activities.extend(repair_activities.registered())
            async with Worker(
                client,
                task_queue=runtime.queue,
                workflows=workflows,
                activities=registered_activities,
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

    def _repair_handle(self, incident_id, candidate_id, run_id):
        if self.repair_activities is None:
            raise HTTPException(503, "Temporal repair recovery is disabled or unavailable")
        if (
            type(incident_id) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", incident_id)
            or type(candidate_id) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", candidate_id)
            or type(run_id) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id)
        ):
            raise HTTPException(422, "Invalid repair recovery run identity")
        return self.client.get_workflow_handle(
            f"repair-recovery:{incident_id}:{candidate_id}:{run_id}"
        )

    async def start_repair(self, request, run_id):
        from ai_quant_os.temporal_repair import RepairRecoveryRequest, start_repair_recovery

        if self.repair_activities is None:
            raise HTTPException(503, "Temporal repair recovery is disabled or unavailable")
        if type(request) is not RepairRecoveryRequest:
            raise HTTPException(422, "Invalid repair recovery request")
        handle = self._repair_handle(request.incident_id, request.candidate_id, run_id)
        try:
            await handle.describe(rpc_timeout=timedelta(seconds=3))
        except RPCError as error:
            if error.status != RPCStatusCode.NOT_FOUND:
                raise HTTPException(503, "Temporal repair service unavailable") from error
        except TimeoutError as error:
            raise HTTPException(503, "Temporal repair service unavailable") from error
        else:
            raise HTTPException(409, "Repair recovery workflow already exists")
        try:
            await start_repair_recovery(
                self.client,
                request,
                run_id=run_id,
                task_queue=self.queue,
            )
        except WorkflowAlreadyStartedError as error:
            raise HTTPException(409, "Repair recovery workflow already exists") from error
        except RPCError as error:
            raise HTTPException(503, "Repair workflow start unconfirmed") from error
        except TimeoutError as error:
            raise HTTPException(503, "Repair workflow start unconfirmed") from error
        return {
            "incident_id": request.incident_id,
            "candidate_id": request.candidate_id,
            "run_id": run_id,
            "state": "SUBMITTED",
            "promotion_allowed": False,
            "production_released": False,
        }

    async def view_repair(self, incident_id, candidate_id, run_id):
        handle = self._repair_handle(incident_id, candidate_id, run_id)
        try:
            description = await handle.describe(rpc_timeout=timedelta(seconds=3))
            if description.status == WorkflowExecutionStatus.COMPLETED:
                result = await asyncio.wait_for(handle.result(), 3)
                stage = result["stage"]
            elif description.status == WorkflowExecutionStatus.RUNNING:
                result = None
                stage = await handle.query("stage", rpc_timeout=timedelta(seconds=3))
            else:
                result, stage = None, None
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, "Unknown repair recovery workflow") from error
            raise HTTPException(503, "Temporal repair service unavailable") from error
        except TimeoutError as error:
            raise HTTPException(503, "Temporal repair service unavailable") from error
        return {
            "incident_id": incident_id,
            "candidate_id": candidate_id,
            "run_id": run_id,
            "state": description.status.name,
            "stage": stage,
            "result": result,
            "promotion_allowed": False,
            "production_released": False,
        }

    async def reconcile_repair(self, incident_id, candidate_id, run_id):
        view = await self.view_repair(incident_id, candidate_id, run_id)
        if view["state"] != "RUNNING" or view["stage"] != "WAIT_FOR_PINNED_RECONCILIATION":
            raise HTTPException(409, "Repair workflow is not waiting for reconciliation")
        try:
            await self._repair_handle(incident_id, candidate_id, run_id).signal(
                "reconcile", rpc_timeout=timedelta(seconds=3)
            )
        except RPCError as error:
            raise HTTPException(503, "Repair reconciliation signal unconfirmed") from error
        return {
            "incident_id": incident_id,
            "candidate_id": candidate_id,
            "run_id": run_id,
            "state": "RECONCILIATION_REQUESTED",
            "promotion_allowed": False,
            "production_released": False,
        }

    async def submit_repair_certificate(self, incident_id, candidate_id, run_id, raw):
        if type(raw) is not bytes or not 1 <= len(raw) <= 65536:
            raise HTTPException(413, "Repair certificate exceeds the 64 KiB limit")
        view = await self.view_repair(incident_id, candidate_id, run_id)
        if view["state"] != "RUNNING" or view["stage"] != "AWAITING_INDEPENDENT_VERIFICATION":
            raise HTTPException(409, "Repair workflow is not awaiting independent verification")
        digest = sha256(raw).hexdigest()
        folder = self.repair_activities._jobs_root.parent / "certificates"
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder_metadata = folder.lstat()
        reparse = bool(
            getattr(folder_metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        )
        if (
            stat.S_ISLNK(folder_metadata.st_mode)
            or reparse
            or not stat.S_ISDIR(folder_metadata.st_mode)
        ):
            raise HTTPException(503, "Repair certificate storage is unavailable")
        workflow_key = sha256(f"{incident_id}:{candidate_id}:{run_id}".encode("ascii")).hexdigest()
        binding_path = folder / f"workflow-{workflow_key}.submission"
        binding = digest.encode("ascii")
        try:
            _persist_repair_artifact(binding_path, binding, limit=64)
        except HTTPException as error:
            if error.status_code == 409 and error.detail == "Repair evidence identity conflict":
                raise HTTPException(
                    409, "A different certificate is already bound to this workflow"
                ) from error
            raise
        path = folder / f"{digest}.json"
        _persist_repair_artifact(path, raw, limit=65536)
        try:
            await self._repair_handle(incident_id, candidate_id, run_id).signal(
                "verification_certificate", digest, rpc_timeout=timedelta(seconds=3)
            )
        except RPCError as error:
            raise HTTPException(503, "Repair certificate signal unconfirmed") from error
        return {
            "incident_id": incident_id,
            "candidate_id": candidate_id,
            "run_id": run_id,
            "certificate_sha256": digest,
            "state": "VERIFICATION_SUBMITTED",
            "promotion_allowed": False,
            "production_released": False,
        }

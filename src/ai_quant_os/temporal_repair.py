"""Durable, fail-closed orchestration for isolated repair evidence review."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import re

from temporalio import activity, workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError


@dataclass(frozen=True)
class RepairRecoveryRequest:
    incident_id: str
    candidate_id: str
    execution_owner: str

    def __post_init__(self):
        if (
            type(self.incident_id) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", self.incident_id)
            or type(self.candidate_id) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", self.candidate_id)
            or type(self.execution_owner) is not str
            or not re.fullmatch(r"[a-f0-9]{32}", self.execution_owner)
        ):
            raise ValueError("Pinned incident, candidate and execution owner required")


@workflow.defn
class RepairRecoveryWorkflow:
    """Observe repair evidence durably; never approve production release."""

    def __init__(self):
        self._stage = "QUEUED"
        self._reconcile_requested = False
        self._certificate_sha256 = None

    @workflow.query(name="stage")
    def stage(self) -> str:
        return self._stage

    @workflow.signal(name="reconcile")
    def reconcile(self) -> None:
        self._reconcile_requested = True

    @workflow.signal(name="verification_certificate")
    def verification_certificate(self, certificate_sha256: str) -> None:
        if self._certificate_sha256 is None:
            self._certificate_sha256 = certificate_sha256

    @workflow.run
    async def run(self, request: RepairRecoveryRequest) -> dict:
        result = {
            "schema_version": "1.0",
            "incident_id": request.incident_id,
            "candidate_id": request.candidate_id,
            "execution_owner": request.execution_owner,
            "stage": "SAFE_STATE_UNCONFIRMED",
            "safety_state": None,
            "snapshot_sha256": None,
            "promotion_allowed": False,
            "production_released": False,
        }
        options = {
            "start_to_close_timeout": timedelta(seconds=30),
            "retry_policy": RetryPolicy(maximum_attempts=1),
            "cancellation_type": workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        }
        try:
            self._stage = "CONTAINING"
            containment = await workflow.execute_activity(
                "repair_recovery_contain", request, **options
            )
            if not self._valid_containment(containment, request):
                self._stage = "SAFE_STATE_UNCONFIRMED"
                return result
            result["safety_state"] = containment["safety_state"]
            self._stage = "CHECKPOINTING_EXECUTION"
            for attempt in range(20):
                checkpoint = await workflow.execute_activity(
                    "repair_execution_checkpoint", request, **options
                )
                if not self._valid_checkpoint(checkpoint, request, result["safety_state"]):
                    self._stage = "REVIEW_REQUIRED"
                    return {**result, "stage": self._stage}
                result["snapshot_sha256"] = checkpoint["snapshot_sha256"]
                if checkpoint["state"] == "EXECUTION_OUTCOME_UNCONFIRMED":
                    self._stage = "WAIT_FOR_PINNED_RECONCILIATION"
                    if attempt == 19:
                        return {**result, "stage": self._stage}
                    self._reconcile_requested = False
                    try:
                        await workflow.wait_condition(
                            lambda: self._reconcile_requested,
                            timeout=timedelta(days=7),
                        )
                    except TimeoutError:
                        return {**result, "stage": self._stage}
                    self._stage = "CHECKPOINTING_EXECUTION"
                    continue
                if checkpoint["execution_state"] == "EXECUTION_FAILED_AWAITING_REVIEW":
                    self._stage = "REPAIR_FAILED_AWAITING_REVIEW"
                    return {**result, "stage": self._stage}
                self._stage = "AWAITING_INDEPENDENT_VERIFICATION"
                try:
                    await workflow.wait_condition(
                        lambda: self._certificate_sha256 is not None,
                        timeout=timedelta(days=7),
                    )
                except TimeoutError:
                    return {**result, "stage": self._stage}
                verification = await workflow.execute_activity(
                    "repair_verification_certificate",
                    (request, self._certificate_sha256),
                    **options,
                )
                if not self._valid_verification(verification, request, result["snapshot_sha256"]):
                    self._stage = "REVIEW_REQUIRED"
                    return {**result, "stage": self._stage}
                self._stage = verification["state"]
                return {
                    **result,
                    "stage": self._stage,
                    "certificate_sha256": verification["certificate_sha256"],
                }
        except ActivityError:
            self._stage = "REVIEW_REQUIRED"
            return {**result, "stage": self._stage}
        self._stage = "WAIT_FOR_PINNED_RECONCILIATION"
        return {**result, "stage": self._stage}

    @staticmethod
    def _valid_containment(value, request):
        return (
            type(value) is dict
            and set(value) == {"incident_id", "safety_state", "audit_valid", "promotion_allowed"}
            and value["incident_id"] == request.incident_id
            and type(value["safety_state"]) is str
            and value["safety_state"] in {"HALT", "NO_NEW_RISK"}
            and value["audit_valid"] is True
            and value["promotion_allowed"] is False
        )

    @staticmethod
    def _valid_checkpoint(value, request, safety_state):
        if (
            type(value) is not dict
            or set(value)
            != {
                "incident_id",
                "candidate_id",
                "owner",
                "snapshot_sha256",
                "state",
                "execution_state",
                "safety_state",
                "candidate_bound",
                "promotion_allowed",
            }
            or value["incident_id"] != request.incident_id
            or value["candidate_id"] != request.candidate_id
            or value["owner"] != request.execution_owner
            or type(value["snapshot_sha256"]) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", value["snapshot_sha256"])
            or value["safety_state"] != safety_state
            or value["candidate_bound"] is not True
            or value["promotion_allowed"] is not False
        ):
            return False
        if value["state"] == "EXECUTION_OUTCOME_UNCONFIRMED":
            return value["execution_state"] is None
        return (
            value["state"] == "EXECUTION_RECORDED"
            and type(value["execution_state"]) is str
            and value["execution_state"]
            in {
                "EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION",
                "EXECUTION_FAILED_AWAITING_REVIEW",
            }
        )

    @staticmethod
    def _valid_verification(value, request, snapshot_sha256):
        return (
            type(value) is dict
            and set(value)
            == {
                "incident_id",
                "candidate_id",
                "snapshot_sha256",
                "certificate_sha256",
                "state",
                "promotion_allowed",
            }
            and value["incident_id"] == request.incident_id
            and value["candidate_id"] == request.candidate_id
            and value["snapshot_sha256"] == snapshot_sha256
            and type(value["certificate_sha256"]) is str
            and re.fullmatch(r"[a-f0-9]{64}", value["certificate_sha256"])
            and type(value["state"]) is str
            and value["state"]
            in {"INDEPENDENT_VERIFICATION_PASSED", "REPAIR_FAILED_AWAITING_REVIEW"}
            and value["promotion_allowed"] is False
        )


class RepairRecoveryActivities:
    """Application-bound activities for health containment and receipt custody."""

    def __init__(self, watchdog, workspace, *, jobs_root: Path, verifier_trust_store=None):
        self._watchdog = watchdog
        self._workspace = workspace
        self._jobs_root = Path(jobs_root).absolute()
        self._verifier_trust_store = verifier_trust_store

    async def _call(self, method, request):
        task = asyncio.create_task(asyncio.to_thread(method, request))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise
                except Exception as error:
                    raise asyncio.CancelledError from error
            raise
        except (ValueError, OSError, RuntimeError) as error:
            raise ApplicationError(
                "Repair recovery activity failed closed; inspect retained health and repair evidence",
                type=type(error).__name__,
                non_retryable=True,
            ) from error

    def _contain(self, request: RepairRecoveryRequest) -> dict:
        from ai_quant_os.risk import SafetyState

        self._watchdog.restore()
        if not self._watchdog.store.integrity().valid:
            raise ValueError("Health incident journal integrity is unavailable")
        incident = next(
            (
                row
                for row in self._watchdog.store.open_incidents()
                if row.incident_id == request.incident_id
            ),
            None,
        )
        if incident is None:
            raise ValueError("Repair recovery requires the exact retained health incident")
        target = SafetyState(incident.safety_state)
        state = self._watchdog.state.safety_state()
        risk_state = self._watchdog.risk.safety_state
        if (
            not self._watchdog.state.audit_integrity().valid
            or list(SafetyState).index(state) < list(SafetyState).index(target)
            or list(SafetyState).index(risk_state) < list(SafetyState).index(target)
        ):
            raise ValueError("Durable critical failure safety state is not confirmed")
        return {
            "incident_id": request.incident_id,
            "safety_state": state.value,
            "audit_valid": True,
            "promotion_allowed": False,
        }

    def _checkpoint(self, request: RepairRecoveryRequest) -> dict:
        from datetime import datetime

        from ai_quant_os.health_contracts import HealthIncident
        from ai_quant_os.repair_containers import read_repair_execution

        snapshot = self._workspace.snapshot(request.candidate_id)
        incident_fields = snapshot.incident
        incident_fields["detected_at"] = datetime.fromisoformat(incident_fields["detected_at"])
        if HealthIncident(**incident_fields).incident_id != request.incident_id:
            raise ValueError("Repair candidate is not bound to the retained incident")
        report = read_repair_execution(self._jobs_root, request.execution_owner)
        receipt = report.receipt
        if receipt is not None and receipt.snapshot_sha256 != snapshot.snapshot_sha256:
            raise ValueError("Execution receipt is not bound to the candidate source snapshot")
        if receipt is None:
            state, execution_state = "EXECUTION_OUTCOME_UNCONFIRMED", None
        else:
            state, execution_state = "EXECUTION_RECORDED", receipt.state
        return {
            "incident_id": request.incident_id,
            "candidate_id": request.candidate_id,
            "owner": request.execution_owner,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "state": state,
            "execution_state": execution_state,
            "safety_state": self._watchdog.state.safety_state().value,
            "candidate_bound": True,
            "promotion_allowed": False,
        }

    def _verify_certificate(self, request_and_hash) -> dict:
        import json
        from hashlib import sha256

        from ai_quant_os.repair_certificates import verify_repair_certificate
        from ai_quant_os.repair_image_inputs import read_repair_image_input

        request, certificate_sha256 = request_and_hash
        if type(request) is dict and set(request) == {
            "incident_id",
            "candidate_id",
            "execution_owner",
        }:
            request = RepairRecoveryRequest(**request)
        if (
            type(request) is not RepairRecoveryRequest
            or type(certificate_sha256) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", certificate_sha256)
            or self._verifier_trust_store is None
        ):
            raise ValueError("Repair certificate verification is not configured")
        recovery = self._checkpoint(request)
        from ai_quant_os.repair_containers import read_repair_execution

        receipt_report = read_repair_execution(self._jobs_root, request.execution_owner)
        receipt = receipt_report.receipt
        if (
            receipt is None
            or receipt.state != "EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION"
        ):
            raise ValueError("Only a successful, retained execution can be independently verified")
        request_path = self._jobs_root / f"repair-execution-{request.execution_owner}.request.json"
        request_raw = read_repair_image_input(request_path, 65536)
        if sha256(request_raw).hexdigest() != receipt.request_sha256:
            raise ValueError("Prepared request does not match the execution receipt")
        try:
            job_request = json.loads(request_raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Prepared request is invalid") from error
        replay_sha256 = job_request.get("replay_sha256")
        if type(replay_sha256) is not str or not re.fullmatch(r"[a-f0-9]{64}", replay_sha256):
            raise ValueError("Independent certificate requires retained incident replay evidence")
        certificate_path = self._jobs_root.parent / "certificates" / f"{certificate_sha256}.json"
        raw = read_repair_image_input(certificate_path, 65536)
        if sha256(raw).hexdigest() != certificate_sha256:
            raise ValueError("Retained certificate content hash does not match signal")
        envelope = json.loads(raw)
        verifier_id = envelope["certificate"]["verifier_id"]
        verifier_source_sha256 = self._verifier_trust_store.verifier_source_sha256[verifier_id]
        verified = verify_repair_certificate(
            self._workspace.snapshot(request.candidate_id),
            raw,
            trusted_public_keys=self._verifier_trust_store.trusted_public_keys,
            replay_sha256=replay_sha256,
            suite_sha256=receipt.suite_sha256,
            request_sha256=receipt.request_sha256,
            incident_id=request.incident_id,
            verifier_source_sha256=verifier_source_sha256,
            image_id=receipt.image_id,
        )
        return {
            "incident_id": request.incident_id,
            "candidate_id": request.candidate_id,
            "snapshot_sha256": recovery["snapshot_sha256"],
            "certificate_sha256": verified.certificate_sha256,
            "state": verified.state,
            "promotion_allowed": False,
        }

    @activity.defn(name="repair_recovery_contain")
    async def contain(self, request: RepairRecoveryRequest) -> dict:
        if type(request) is not RepairRecoveryRequest:
            raise ApplicationError("Exact repair recovery request required", non_retryable=True)
        return await self._call(self._contain, request)

    @activity.defn(name="repair_execution_checkpoint")
    async def checkpoint(self, request: RepairRecoveryRequest) -> dict:
        if type(request) is not RepairRecoveryRequest:
            raise ApplicationError("Exact repair recovery request required", non_retryable=True)
        return await self._call(self._checkpoint, request)

    @activity.defn(name="repair_verification_certificate")
    async def verify_certificate(self, request_and_hash) -> dict:
        return await self._call(self._verify_certificate, request_and_hash)

    def registered(self):
        return [self.contain, self.checkpoint, self.verify_certificate]


async def start_repair_recovery(
    client,
    request: RepairRecoveryRequest,
    *,
    run_id: str,
    task_queue: str,
):
    """Start one durable checkpoint observer for an exact candidate/incident pair."""
    if (
        type(request) is not RepairRecoveryRequest
        or type(run_id) is not str
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id)
        or type(task_queue) is not str
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_queue)
    ):
        raise ValueError("Exact local repair workflow identity required")
    return await client.start_workflow(
        RepairRecoveryWorkflow.run,
        request,
        id=f"repair-recovery:{request.incident_id}:{request.candidate_id}:{run_id}",
        task_queue=task_queue,
        execution_timeout=timedelta(days=30),
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        rpc_timeout=timedelta(seconds=10),
    )

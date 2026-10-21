"""A01 0.5.0 task routing adds an explicit, advisory incident-review cohort."""

import json
from hashlib import sha256
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.supervisor_agent import DeferredTarget, SpecialistCall, WakeTarget, coordinate_plan
from ai_quant_os.supervisor_sources import SPECIALISTS
from ai_quant_os.task_supervisor import (
    TaskDeclaration as PreviousTaskDeclaration,
    TaskRegistryEvidence as PreviousTaskRegistryEvidence,
    TaskSupervisorEvidence as PreviousTaskSupervisorEvidence,
    plan_task_targets,
    require_declared_plan_context,
)

TaskKind = Literal[
    "MARKET_REVIEW", "DATA_REVIEW", "EVENT_REVIEW", "DRIFT_REVIEW", "INCIDENT_REVIEW"
]
TASK_AGENTS = {
    "MARKET_REVIEW": SPECIALISTS,
    "DATA_REVIEW": ("A07",),
    "EVENT_REVIEW": ("A06",),
    "DRIFT_REVIEW": ("A14",),
    "INCIDENT_REVIEW": ("A18",),
}
TASK_SPECIALISTS = (*SPECIALISTS, "A14", "A18")


class IncidentSupervisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1, max_length=128)


class TaskDeclaration(PreviousTaskDeclaration):
    task_kind: TaskKind


def executable(agent: str, version: str) -> bool:
    return (agent == "A01" and version == "0.5.0") or (
        agent in TASK_SPECIALISTS and version == "0.2.0"
    )


class TaskRegistryEvidence(PreviousTaskRegistryEvidence):
    @model_validator(mode="after")
    def exact_catalog(self):
        keys = [(row.manifest.agent_id, row.manifest.agent_version) for row in self.entries]
        if keys != sorted(set(keys)):
            raise ValueError("Supervisor catalog must have unique ordered versions")
        for row in self.entries:
            expected = executable(row.manifest.agent_id, row.manifest.agent_version)
            if row.executable != expected or (
                expected and row.manifest.implementation_status == "PLANNED"
            ):
                raise ValueError("Unsupported task supervisor execution catalog")
        return self


class TaskSupervisorEvidence(PreviousTaskSupervisorEvidence):
    agent_registry: TaskRegistryEvidence
    task_spec: TaskDeclaration


def read_context(context: CompiledContext) -> TaskSupervisorEvidence:
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A01",
        "0.5.0",
        "PAPER",
        "READY",
    ):
        raise ValueError("Incident task supervisor requires its complete paper context")
    evidence = TaskSupervisorEvidence.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    world = evidence.world_state.world
    if (
        world.as_of > context.created_at
        or context.valid_until > world.valid_until
        or evidence.task_spec.task_id != context.task_id
    ):
        raise ValueError("Incident task supervisor source identity or expiry mismatch")
    return evidence


class TaskDeferredTarget(DeferredTarget):
    reason: Literal["PLANNED", "UNSUPPORTED", "QUARANTINED", "HEALTH_ERROR", "NOT_REQUESTED"]


def wake_plan(evidence: TaskSupervisorEvidence):
    return plan_task_targets(
        evidence,
        TASK_AGENTS[evidence.task_spec.task_kind],
        "0.5.0",
        deferred_type=TaskDeferredTarget,
    )


def child_run_id(context_ref: str, agent_id: str) -> str:
    if not context_ref.startswith("agent-context:") or agent_id not in TASK_SPECIALISTS:
        raise ValueError("Invalid incident supervisor child identity")
    return "supervisor_" + sha256(f"{context_ref}:{agent_id}:0.2.0".encode()).hexdigest()


class TaskSpecialistCall(SpecialistCall):
    @model_validator(mode="after")
    def receipt_claims(self):
        if (self.status == "COMPLETED") != (self.output_sha256 is not None):
            raise ValueError("Supervisor output claim/status mismatch")
        not_started = self.status.startswith("NOT_STARTED")
        if not_started != (self.context_ref is None) or not_started != (self.valid_until is None):
            raise ValueError("Supervisor receipt presence/status mismatch")
        if self.agent_id not in TASK_SPECIALISTS or self.agent_version != "0.2.0":
            raise ValueError("Task supervisor cannot call this agent version")
        return self

    @classmethod
    def from_receipt(cls, receipt):
        return cls(
            agent_id=receipt.agent_id,
            agent_version=receipt.agent_version,
            manifest_ref=receipt.manifest_ref,
            run_id=receipt.run_id,
            status=receipt.status,
            context_ref=receipt.context_ref,
            valid_until=receipt.valid_until,
            output_sha256=sha256(receipt.output_json.encode()).hexdigest()
            if receipt.output_json
            else None,
        )


class TaskPlan(AgentEnvelope):
    agent_id: Literal["A01"] = "A01"
    agent_version: Literal["0.5.0"] = "0.5.0"
    output_type: Literal["TaskPlan"] = "TaskPlan"
    policy_version: Literal["paper-task-supervisor-v2"] = "paper-task-supervisor-v2"
    task_kind: TaskKind
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    hard_gate_owner: Literal["RiskKernel"] = "RiskKernel"
    max_calls: int = Field(strict=True, ge=1, le=7)
    dispatch_mode: Literal["SEQUENTIAL_PARENT_DEADLINE"] = "SEQUENTIAL_PARENT_DEADLINE"
    parent_context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")
    world_state_version: str = Field(pattern=r"^world:[a-f0-9]{64}$")
    health_status: Literal["UNAVAILABLE", "RESTORED_ONLY", "MONITORED", "ERROR"]
    selected: tuple[WakeTarget, ...] = Field(max_length=7)
    deferred: tuple[TaskDeferredTarget, ...] = Field(max_length=128)
    calls: tuple[TaskSpecialistCall, ...] = Field(max_length=7)

    @model_validator(mode="after")
    def task_ceiling(self):
        if self.max_calls != len(TASK_AGENTS[self.task_kind]) or len(self.calls) > self.max_calls:
            raise ValueError("Supervisor call ceiling differs from declared task")
        return self

    def require_context(self, context: CompiledContext) -> None:
        require_declared_plan_context(
            self,
            context,
            read_context=read_context,
            wake_plan=wake_plan,
            child_run_id=child_run_id,
        )


async def coordinate_task(invocation: AgentInvocation) -> TaskPlan:
    evidence = read_context(invocation.context)
    selected, deferred = wake_plan(evidence)
    return await coordinate_plan(
        invocation,
        evidence,
        selected,
        deferred,
        plan_type=TaskPlan,
        call_type=TaskSpecialistCall,
        plan_fields={
            "task_kind": evidence.task_spec.task_kind,
            "max_calls": len(TASK_AGENTS[evidence.task_spec.task_kind]),
        },
    )


def task_supervisor_incident_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(registry.get("A01", "0.5.0").manifest_ref, TaskPlan, coordinate_task)

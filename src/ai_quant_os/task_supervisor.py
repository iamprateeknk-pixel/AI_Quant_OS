"""A01 0.4.0 declared task routing; caller intent never grants arbitrary child authority."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.supervisor_agent import DeferredTarget, WakeTarget, coordinate_plan
from ai_quant_os.supervisor_drift import (
    DRIFT_SPECIALISTS,
    DriftRegistryEvidence,
    DriftSpecialistCall,
    DriftSupervisorEvidence,
    TaskPlan as PreviousTaskPlan,
)
from ai_quant_os.supervisor_sources import SPECIALISTS, SupervisorObservation, effective_tools

TaskKind = Literal["MARKET_REVIEW", "DATA_REVIEW", "EVENT_REVIEW", "DRIFT_REVIEW"]
TASK_AGENTS = {
    "MARKET_REVIEW": SPECIALISTS,
    "DATA_REVIEW": ("A07",),
    "EVENT_REVIEW": ("A06",),
    "DRIFT_REVIEW": ("A14",),
}


class TaskSupervisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1, max_length=128)
    task_kind: TaskKind


class TaskDeclaration(SupervisorObservation):
    task_id: str = Field(min_length=1, max_length=128)
    task_kind: TaskKind


def executable(agent: str, version: str) -> bool:
    return (agent == "A01" and version == "0.4.0") or (
        agent in DRIFT_SPECIALISTS and version == "0.2.0"
    )


class TaskRegistryEvidence(DriftRegistryEvidence):
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


class TaskSupervisorEvidence(DriftSupervisorEvidence):
    agent_registry: TaskRegistryEvidence
    task_spec: TaskDeclaration

    @model_validator(mode="after")
    def bound_declaration(self):
        world = self.world_state.world
        if (self.task_spec.as_of, self.task_spec.world_state_version) != (
            world.as_of,
            world.world_state_version,
        ):
            raise ValueError("Task declaration refers to another observation")
        return self


def read_context(context: CompiledContext) -> TaskSupervisorEvidence:
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A01",
        "0.4.0",
        "PAPER",
        "READY",
    ):
        raise ValueError("Task supervisor requires its complete paper context")
    evidence = TaskSupervisorEvidence.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    world = evidence.world_state.world
    if (
        world.as_of > context.created_at
        or context.valid_until > world.valid_until
        or evidence.task_spec.task_id != context.task_id
    ):
        raise ValueError("Task supervisor source identity or expiry mismatch")
    return evidence


class TaskDeferredTarget(DeferredTarget):
    reason: Literal["PLANNED", "UNSUPPORTED", "QUARANTINED", "HEALTH_ERROR", "NOT_REQUESTED"]


def plan_task_targets(
    evidence: TaskSupervisorEvidence,
    wanted: tuple[str, ...],
    supervisor_version: str,
    deferred_type: type[DeferredTarget] = TaskDeferredTarget,
):
    selected, deferred = {}, []
    for row in evidence.agent_registry.entries:
        agent, version = row.manifest.agent_id, row.manifest.agent_version
        if (agent, version) == ("A01", supervisor_version):
            continue
        target = dict(agent_id=agent, agent_version=version, manifest_ref=row.manifest.manifest_ref)
        reason = (
            "QUARANTINED"
            if row.lifecycle == "QUARANTINED"
            else "PLANNED"
            if row.manifest.implementation_status == "PLANNED"
            else "UNSUPPORTED"
            if not row.executable
            else "HEALTH_ERROR"
            if evidence.health.status == "ERROR"
            else "NOT_REQUESTED"
            if agent not in wanted
            else None
        )
        if reason:
            deferred.append(deferred_type(**target, reason=reason))
        else:
            if not effective_tools(row):
                raise ValueError("Requested specialist has no current read permissions")
            selected[agent] = WakeTarget(**target)
    return tuple(selected[agent] for agent in wanted if agent in selected), tuple(deferred)


def require_declared_plan_context(plan, context, *, read_context, wake_plan, child_run_id):
    AgentEnvelope.require_context(plan, context)
    evidence = read_context(context)
    selected, deferred = wake_plan(evidence)
    if (
        plan.parent_context_ref,
        plan.world_state_version,
        plan.health_status,
        plan.task_kind,
        plan.selected,
        plan.deferred,
    ) != (
        context.context_ref,
        evidence.world_state.world.world_state_version,
        evidence.health.status,
        evidence.task_spec.task_kind,
        selected,
        deferred,
    ):
        raise ValueError("Supervisor plan differs from original source declaration")
    identities = [(row.agent_id, row.agent_version, row.manifest_ref) for row in plan.selected]
    if identities != [
        (row.agent_id, row.agent_version, row.manifest_ref) for row in plan.calls
    ] or len(set(identities)) != len(identities):
        raise ValueError("Supervisor calls do not match wake plan")
    for row in plan.calls:
        if row.run_id != child_run_id(plan.parent_context_ref, row.agent_id):
            raise ValueError("Supervisor child identity mismatch")
        if row.status == "COMPLETED" and plan.valid_until > row.valid_until:
            raise ValueError("Supervisor expiry exceeds specialist evidence")


def wake_plan(evidence: TaskSupervisorEvidence):
    return plan_task_targets(evidence, TASK_AGENTS[evidence.task_spec.task_kind], "0.4.0")


class TaskPlan(PreviousTaskPlan):
    agent_version: Literal["0.4.0"] = "0.4.0"
    policy_version: Literal["paper-task-supervisor-v1"] = "paper-task-supervisor-v1"
    task_kind: TaskKind
    max_calls: int = Field(strict=True, ge=1, le=7)
    deferred: tuple[TaskDeferredTarget, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def task_ceiling(self):
        if self.max_calls != len(TASK_AGENTS[self.task_kind]) or len(self.calls) > self.max_calls:
            raise ValueError("Supervisor call ceiling differs from declared task")
        return self

    def require_context(self, context: CompiledContext) -> None:
        AgentEnvelope.require_context(self, context)
        evidence = read_context(context)
        selected, deferred = wake_plan(evidence)
        if (
            self.parent_context_ref,
            self.world_state_version,
            self.health_status,
            self.task_kind,
            self.selected,
            self.deferred,
        ) != (
            context.context_ref,
            evidence.world_state.world.world_state_version,
            evidence.health.status,
            evidence.task_spec.task_kind,
            selected,
            deferred,
        ):
            raise ValueError("Task supervisor plan differs from original source declaration")


async def coordinate_task(invocation: AgentInvocation) -> TaskPlan:
    evidence = read_context(invocation.context)
    selected, deferred = wake_plan(evidence)
    return await coordinate_plan(
        invocation,
        evidence,
        selected,
        deferred,
        plan_type=TaskPlan,
        call_type=DriftSpecialistCall,
        plan_fields={
            "task_kind": evidence.task_spec.task_kind,
            "max_calls": len(TASK_AGENTS[evidence.task_spec.task_kind]),
        },
    )


def task_supervisor_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(registry.get("A01", "0.4.0").manifest_ref, TaskPlan, coordinate_task)

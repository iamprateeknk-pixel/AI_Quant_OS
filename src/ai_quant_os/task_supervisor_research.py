"""A01 0.6.0 coordinates the isolated A08 hypothesis-research mission."""

import json
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.supervisor_agent import DeferredTarget, SpecialistCall, WakeTarget, coordinate_plan
from ai_quant_os.task_supervisor import plan_task_targets, require_declared_plan_context
from ai_quant_os.agent_permissions import READ_TOOLS
from ai_quant_os.macro_sources import MacroWorldEvidence
from ai_quant_os.supervisor_sources import (
    CatalogEntry,
    HealthEvidence,
    PermissionEntry,
    SupervisorObservation,
)

TaskKind = Literal["HYPOTHESIS_RESEARCH"]
TASK_AGENTS = {"HYPOTHESIS_RESEARCH": ("A08",)}
ValidationTaskKind = Literal["VALIDATION_REVIEW"]
ConstructionTaskKind = Literal["STRATEGY_CONSTRUCTION"]


class ResearchSupervisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1, max_length=128)


class TaskDeclaration(SupervisorObservation):
    task_id: str = Field(min_length=1, max_length=128)
    task_kind: TaskKind


def executable(agent: str, version: str) -> bool:
    return (agent == "A01" and version == "0.6.0") or (agent, version) == ("A08", "0.2.0")


class TaskRegistryEvidence(SupervisorObservation):
    entries: tuple[CatalogEntry, ...] = Field(min_length=1, max_length=128)

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
                raise ValueError("Unsupported research supervisor execution catalog")
        return self


class SandboxPermissionsEvidence(SupervisorObservation):
    scope: Literal["SANDBOX"] = "SANDBOX"
    entries: tuple[PermissionEntry, ...] = Field(min_length=1, max_length=128)


def sandbox_effective_tools(entry: CatalogEntry) -> tuple[str, ...]:
    if entry.lifecycle == "QUARANTINED":
        return ()
    allowed = set(entry.manifest.allowed_tools)
    if entry.lifecycle == "LIMITED":
        allowed &= READ_TOOLS
    return tuple(sorted(allowed))


class TaskSupervisorEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_registry: TaskRegistryEvidence
    world_state: MacroWorldEvidence
    health: HealthEvidence
    permissions: SandboxPermissionsEvidence
    task_spec: TaskDeclaration

    @model_validator(mode="after")
    def bound_snapshot(self):
        world = self.world_state.world
        for observation in (self.agent_registry, self.health, self.permissions, self.task_spec):
            if (observation.as_of, observation.world_state_version) != (
                world.as_of,
                world.world_state_version,
            ):
                raise ValueError("Supervisor evidence refers to different observations")
        expected = tuple(
            PermissionEntry(
                manifest_ref=row.manifest.manifest_ref,
                tools=sandbox_effective_tools(row),
            )
            for row in self.agent_registry.entries
        )
        if self.permissions.entries != expected:
            raise ValueError("Supervisor permissions do not match sandbox catalog lifecycle")
        return self


def read_context(context: CompiledContext) -> TaskSupervisorEvidence:
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A01",
        "0.6.0",
        "SANDBOX",
        "READY",
    ):
        raise ValueError("Research task supervisor requires its complete sandbox context")
    evidence = TaskSupervisorEvidence.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    world = evidence.world_state.world
    if (
        world.as_of > context.created_at
        or context.valid_until > world.valid_until
        or evidence.task_spec.task_id != context.task_id
    ):
        raise ValueError("Research task supervisor source identity or expiry mismatch")
    return evidence


class TaskDeferredTarget(DeferredTarget):
    reason: Literal["PLANNED", "UNSUPPORTED", "QUARANTINED", "HEALTH_ERROR", "NOT_REQUESTED"]


def wake_plan(evidence: TaskSupervisorEvidence):
    return plan_task_targets(
        evidence,
        TASK_AGENTS[evidence.task_spec.task_kind],
        "0.6.0",
        deferred_type=TaskDeferredTarget,
    )


def child_run_id(context_ref: str, agent_id: str) -> str:
    if not context_ref.startswith("agent-context:") or agent_id != "A08":
        raise ValueError("Invalid research supervisor child identity")
    return "supervisor_" + sha256(f"{context_ref}:{agent_id}:0.2.0".encode()).hexdigest()


class TaskSpecialistCall(SpecialistCall):
    @model_validator(mode="after")
    def receipt_claims(self):
        if (self.status == "COMPLETED") != (self.output_sha256 is not None):
            raise ValueError("Supervisor output claim/status mismatch")
        not_started = self.status.startswith("NOT_STARTED")
        if not_started != (self.context_ref is None) or not_started != (self.valid_until is None):
            raise ValueError("Supervisor receipt presence/status mismatch")
        if (self.agent_id, self.agent_version) != ("A08", "0.2.0"):
            raise ValueError("Research supervisor cannot call this agent version")
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
    agent_version: Literal["0.6.0"] = "0.6.0"
    output_type: Literal["TaskPlan"] = "TaskPlan"
    policy_version: Literal["sandbox-hypothesis-supervisor-v1"] = "sandbox-hypothesis-supervisor-v1"
    task_kind: TaskKind
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    hard_gate_owner: Literal["RiskKernel"] = "RiskKernel"
    max_calls: int = Field(strict=True, ge=1, le=1)
    dispatch_mode: Literal["SEQUENTIAL_PARENT_DEADLINE"] = "SEQUENTIAL_PARENT_DEADLINE"
    parent_context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")
    world_state_version: str = Field(pattern=r"^world:[a-f0-9]{64}$")
    health_status: Literal["UNAVAILABLE", "RESTORED_ONLY", "MONITORED", "ERROR"]
    selected: tuple[WakeTarget, ...] = Field(max_length=1)
    deferred: tuple[TaskDeferredTarget, ...] = Field(max_length=128)
    calls: tuple[TaskSpecialistCall, ...] = Field(max_length=1)

    @model_validator(mode="after")
    def task_ceiling(self):
        if self.max_calls != 1 or len(self.calls) > self.max_calls:
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
        plan_fields={"task_kind": evidence.task_spec.task_kind, "max_calls": 1},
    )


def task_supervisor_research_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(registry.get("A01", "0.6.0").manifest_ref, TaskPlan, coordinate_task)


def executable_v3(agent: str, version: str) -> bool:
    return (agent, version) in {("A01", "0.7.0"), ("A08", "0.3.0")}


class TaskRegistryEvidenceV3(SupervisorObservation):
    entries: tuple[CatalogEntry, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def exact_catalog(self):
        keys = [(row.manifest.agent_id, row.manifest.agent_version) for row in self.entries]
        if keys != sorted(set(keys)):
            raise ValueError("Supervisor catalog must have unique ordered versions")
        for row in self.entries:
            expected = executable_v3(row.manifest.agent_id, row.manifest.agent_version)
            if row.executable != expected or (
                expected and row.manifest.implementation_status == "PLANNED"
            ):
                raise ValueError("Unsupported v3 research supervisor execution catalog")
        return self


class TaskSupervisorEvidenceV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_registry: TaskRegistryEvidenceV3
    world_state: MacroWorldEvidence
    health: HealthEvidence
    permissions: SandboxPermissionsEvidence
    task_spec: TaskDeclaration

    @model_validator(mode="after")
    def bound_snapshot(self):
        world = self.world_state.world
        for observation in (self.agent_registry, self.health, self.permissions, self.task_spec):
            if (observation.as_of, observation.world_state_version) != (
                world.as_of,
                world.world_state_version,
            ):
                raise ValueError("Supervisor evidence refers to different observations")
        expected = tuple(
            PermissionEntry(
                manifest_ref=row.manifest.manifest_ref, tools=sandbox_effective_tools(row)
            )
            for row in self.agent_registry.entries
        )
        if self.permissions.entries != expected:
            raise ValueError("Supervisor permissions do not match sandbox catalog lifecycle")
        return self


def read_context_v3(context: CompiledContext) -> TaskSupervisorEvidenceV3:
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A01",
        "0.7.0",
        "SANDBOX",
        "READY",
    ):
        raise ValueError("Research task supervisor v3 requires its complete sandbox context")
    evidence = TaskSupervisorEvidenceV3.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    world = evidence.world_state.world
    if (
        world.as_of > context.created_at
        or context.valid_until > world.valid_until
        or evidence.task_spec.task_id != context.task_id
    ):
        raise ValueError("Research task supervisor source identity or expiry mismatch")
    return evidence


def wake_plan_v3(evidence: TaskSupervisorEvidenceV3):
    return plan_task_targets(
        evidence,
        TASK_AGENTS[evidence.task_spec.task_kind],
        "0.7.0",
        deferred_type=TaskDeferredTarget,
    )


def child_run_id_v3(context_ref: str, agent_id: str) -> str:
    if not context_ref.startswith("agent-context:") or agent_id != "A08":
        raise ValueError("Invalid research supervisor v3 child identity")
    return "supervisor_" + sha256(f"{context_ref}:{agent_id}:0.3.0".encode()).hexdigest()


class TaskSpecialistCallV3(SpecialistCall):
    @model_validator(mode="after")
    def receipt_claims(self):
        if (self.status == "COMPLETED") != (self.output_sha256 is not None):
            raise ValueError("Supervisor output claim/status mismatch")
        not_started = self.status.startswith("NOT_STARTED")
        if not_started != (self.context_ref is None) or not_started != (self.valid_until is None):
            raise ValueError("Supervisor receipt presence/status mismatch")
        if (self.agent_id, self.agent_version) != ("A08", "0.3.0"):
            raise ValueError("Research supervisor cannot call this agent version")
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


class TaskPlanV3(AgentEnvelope):
    agent_id: Literal["A01"] = "A01"
    agent_version: Literal["0.7.0"] = "0.7.0"
    output_type: Literal["TaskPlanV3"] = "TaskPlanV3"
    policy_version: Literal["sandbox-hypothesis-supervisor-v2"] = "sandbox-hypothesis-supervisor-v2"
    task_kind: TaskKind
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    hard_gate_owner: Literal["RiskKernel"] = "RiskKernel"
    max_calls: int = Field(strict=True, ge=1, le=1)
    dispatch_mode: Literal["SEQUENTIAL_PARENT_DEADLINE"] = "SEQUENTIAL_PARENT_DEADLINE"
    parent_context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")
    world_state_version: str = Field(pattern=r"^world:[a-f0-9]{64}$")
    health_status: Literal["UNAVAILABLE", "RESTORED_ONLY", "MONITORED", "ERROR"]
    selected: tuple[WakeTarget, ...] = Field(max_length=1)
    deferred: tuple[TaskDeferredTarget, ...] = Field(max_length=128)
    calls: tuple[TaskSpecialistCallV3, ...] = Field(max_length=1)

    @model_validator(mode="after")
    def task_ceiling(self):
        if self.max_calls != 1 or len(self.calls) > self.max_calls:
            raise ValueError("Supervisor call ceiling differs from declared task")
        return self

    def require_context(self, context: CompiledContext) -> None:
        require_declared_plan_context(
            self,
            context,
            read_context=read_context_v3,
            wake_plan=wake_plan_v3,
            child_run_id=child_run_id_v3,
        )


async def coordinate_task_v3(invocation: AgentInvocation) -> TaskPlanV3:
    evidence = read_context_v3(invocation.context)
    selected, deferred = wake_plan_v3(evidence)
    return await coordinate_plan(
        invocation,
        evidence,
        selected,
        deferred,
        plan_type=TaskPlanV3,
        call_type=TaskSpecialistCallV3,
        plan_fields={"task_kind": evidence.task_spec.task_kind, "max_calls": 1},
    )


def task_supervisor_research_implementation_v3(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A01", "0.7.0").manifest_ref, TaskPlanV3, coordinate_task_v3
    )


class ValidationResearchSupervisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    artifact_ref: str = Field(pattern=r"^candidate-(validation|prerequisite-failure):[a-f0-9]{64}$")


class ValidationTaskDeclaration(SupervisorObservation):
    task_id: str = Field(min_length=1, max_length=128)
    task_kind: ValidationTaskKind
    artifact_ref: str = Field(pattern=r"^candidate-(validation|prerequisite-failure):[a-f0-9]{64}$")


def executable_v4(agent: str, version: str) -> bool:
    return (agent, version) in {("A01", "0.8.0"), ("A10", "0.2.0")}


class TaskRegistryEvidenceV4(SupervisorObservation):
    entries: tuple[CatalogEntry, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def exact_catalog(self):
        keys = [(row.manifest.agent_id, row.manifest.agent_version) for row in self.entries]
        if keys != sorted(set(keys)):
            raise ValueError("Supervisor catalog must have unique ordered versions")
        for row in self.entries:
            expected = executable_v4(row.manifest.agent_id, row.manifest.agent_version)
            if row.executable != expected or (
                expected and row.manifest.implementation_status == "PLANNED"
            ):
                raise ValueError("Unsupported v4 validation supervisor execution catalog")
        return self


class TaskSupervisorEvidenceV4(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_registry: TaskRegistryEvidenceV4
    world_state: MacroWorldEvidence
    health: HealthEvidence
    permissions: SandboxPermissionsEvidence
    task_spec: ValidationTaskDeclaration

    @model_validator(mode="after")
    def bound_snapshot(self):
        world = self.world_state.world
        for observation in (self.agent_registry, self.health, self.permissions, self.task_spec):
            if (observation.as_of, observation.world_state_version) != (
                world.as_of,
                world.world_state_version,
            ):
                raise ValueError("Supervisor evidence refers to different observations")
        expected = tuple(
            PermissionEntry(
                manifest_ref=row.manifest.manifest_ref, tools=sandbox_effective_tools(row)
            )
            for row in self.agent_registry.entries
        )
        if self.permissions.entries != expected:
            raise ValueError("Supervisor permissions do not match sandbox catalog lifecycle")
        return self


def read_context_v4(context: CompiledContext) -> TaskSupervisorEvidenceV4:
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A01",
        "0.8.0",
        "SANDBOX",
        "READY",
    ):
        raise ValueError("Validation supervisor requires its complete sandbox context")
    evidence = TaskSupervisorEvidenceV4.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    world = evidence.world_state.world
    if (
        world.as_of > context.created_at
        or context.valid_until > world.valid_until
        or evidence.task_spec.task_id != context.task_id
    ):
        raise ValueError("Validation supervisor source identity or expiry mismatch")
    return evidence


def wake_plan_v4(evidence: TaskSupervisorEvidenceV4):
    return plan_task_targets(
        evidence,
        ("A10",),
        "0.8.0",
        deferred_type=TaskDeferredTarget,
    )


def child_run_id_v4(context_ref: str, agent_id: str) -> str:
    if not context_ref.startswith("agent-context:") or agent_id != "A10":
        raise ValueError("Invalid validation supervisor child identity")
    return "supervisor_" + sha256(f"{context_ref}:{agent_id}:0.2.0".encode()).hexdigest()


class TaskSpecialistCallV4(SpecialistCall):
    @model_validator(mode="after")
    def receipt_claims(self):
        if (self.status == "COMPLETED") != (self.output_sha256 is not None):
            raise ValueError("Supervisor output claim/status mismatch")
        not_started = self.status.startswith("NOT_STARTED")
        if not_started != (self.context_ref is None) or not_started != (self.valid_until is None):
            raise ValueError("Supervisor receipt presence/status mismatch")
        if (self.agent_id, self.agent_version) != ("A10", "0.2.0"):
            raise ValueError("Validation supervisor cannot call this agent version")
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


class TaskPlanV4(TaskPlanV3):
    agent_version: Literal["0.8.0"] = "0.8.0"
    output_type: Literal["TaskPlanV4"] = "TaskPlanV4"
    policy_version: Literal["sandbox-validation-supervisor-v1"] = "sandbox-validation-supervisor-v1"
    task_kind: ValidationTaskKind
    calls: tuple[TaskSpecialistCallV4, ...] = Field(max_length=1)

    def require_context(self, context: CompiledContext) -> None:
        require_declared_plan_context(
            self,
            context,
            read_context=read_context_v4,
            wake_plan=wake_plan_v4,
            child_run_id=child_run_id_v4,
        )


async def coordinate_task_v4(invocation: AgentInvocation) -> TaskPlanV4:
    evidence = read_context_v4(invocation.context)
    selected, deferred = wake_plan_v4(evidence)
    return await coordinate_plan(
        invocation,
        evidence,
        selected,
        deferred,
        plan_type=TaskPlanV4,
        call_type=TaskSpecialistCallV4,
        plan_fields={"task_kind": evidence.task_spec.task_kind, "max_calls": 1},
    )


def task_supervisor_research_implementation_v4(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A01", "0.8.0").manifest_ref, TaskPlanV4, coordinate_task_v4
    )


class ConstructionResearchSupervisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    review_ref: str = Field(pattern=r"^hypothesis-review:[a-f0-9]{64}$")


class ConstructionTaskDeclaration(SupervisorObservation):
    task_id: str = Field(min_length=1, max_length=128)
    task_kind: ConstructionTaskKind
    review_ref: str = Field(pattern=r"^hypothesis-review:[a-f0-9]{64}$")


def executable_v5(agent: str, version: str) -> bool:
    return (agent, version) in {("A01", "0.9.0"), ("A09", "0.2.0")}


class TaskRegistryEvidenceV5(SupervisorObservation):
    entries: tuple[CatalogEntry, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def exact_catalog(self):
        keys = [(row.manifest.agent_id, row.manifest.agent_version) for row in self.entries]
        if keys != sorted(set(keys)):
            raise ValueError("Supervisor catalog must have unique ordered versions")
        for row in self.entries:
            expected = executable_v5(row.manifest.agent_id, row.manifest.agent_version)
            if row.executable != expected or (
                expected and row.manifest.implementation_status == "PLANNED"
            ):
                raise ValueError("Unsupported v5 construction supervisor execution catalog")
        return self


class TaskSupervisorEvidenceV5(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_registry: TaskRegistryEvidenceV5
    world_state: MacroWorldEvidence
    health: HealthEvidence
    permissions: SandboxPermissionsEvidence
    task_spec: ConstructionTaskDeclaration

    @model_validator(mode="after")
    def bound_snapshot(self):
        world = self.world_state.world
        for observation in (self.agent_registry, self.health, self.permissions, self.task_spec):
            if (observation.as_of, observation.world_state_version) != (
                world.as_of,
                world.world_state_version,
            ):
                raise ValueError("Supervisor evidence refers to different observations")
        expected = tuple(
            PermissionEntry(
                manifest_ref=row.manifest.manifest_ref, tools=sandbox_effective_tools(row)
            )
            for row in self.agent_registry.entries
        )
        if self.permissions.entries != expected:
            raise ValueError("Supervisor permissions do not match sandbox catalog lifecycle")
        return self


def read_context_v5(context: CompiledContext) -> TaskSupervisorEvidenceV5:
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A01",
        "0.9.0",
        "SANDBOX",
        "READY",
    ):
        raise ValueError("Construction supervisor requires its complete sandbox context")
    evidence = TaskSupervisorEvidenceV5.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    world = evidence.world_state.world
    if (
        world.as_of > context.created_at
        or context.valid_until > world.valid_until
        or evidence.task_spec.task_id != context.task_id
    ):
        raise ValueError("Construction supervisor source identity or expiry mismatch")
    return evidence


def wake_plan_v5(evidence: TaskSupervisorEvidenceV5):
    return plan_task_targets(
        evidence,
        ("A09",),
        "0.9.0",
        deferred_type=TaskDeferredTarget,
    )


def child_run_id_v5(context_ref: str, agent_id: str) -> str:
    if not context_ref.startswith("agent-context:") or agent_id != "A09":
        raise ValueError("Invalid construction supervisor child identity")
    return "supervisor_" + sha256(f"{context_ref}:{agent_id}:0.2.0".encode()).hexdigest()


class TaskSpecialistCallV5(SpecialistCall):
    @model_validator(mode="after")
    def receipt_claims(self):
        if (self.status == "COMPLETED") != (self.output_sha256 is not None):
            raise ValueError("Supervisor output claim/status mismatch")
        not_started = self.status.startswith("NOT_STARTED")
        if not_started != (self.context_ref is None) or not_started != (self.valid_until is None):
            raise ValueError("Supervisor receipt presence/status mismatch")
        if (self.agent_id, self.agent_version) != ("A09", "0.2.0"):
            raise ValueError("Construction supervisor cannot call this agent version")
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


class TaskPlanV5(TaskPlanV3):
    agent_version: Literal["0.9.0"] = "0.9.0"
    output_type: Literal["TaskPlanV5"] = "TaskPlanV5"
    policy_version: Literal["sandbox-reviewed-construction-supervisor-v1"] = (
        "sandbox-reviewed-construction-supervisor-v1"
    )
    task_kind: ConstructionTaskKind
    calls: tuple[TaskSpecialistCallV5, ...] = Field(max_length=1)

    def require_context(self, context: CompiledContext) -> None:
        require_declared_plan_context(
            self,
            context,
            read_context=read_context_v5,
            wake_plan=wake_plan_v5,
            child_run_id=child_run_id_v5,
        )


async def coordinate_task_v5(invocation: AgentInvocation) -> TaskPlanV5:
    evidence = read_context_v5(invocation.context)
    selected, deferred = wake_plan_v5(evidence)
    return await coordinate_plan(
        invocation,
        evidence,
        selected,
        deferred,
        plan_type=TaskPlanV5,
        call_type=TaskSpecialistCallV5,
        plan_fields={"task_kind": evidence.task_spec.task_kind, "max_calls": 1},
    )


def task_supervisor_research_implementation_v5(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A01", "0.9.0").manifest_ref, TaskPlanV5, coordinate_task_v5
    )

"""Immutable A01 0.3.0 paper orchestration including source-bound A14 drift."""

import json
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.supervisor_agent import (
    TaskPlan as LegacyTaskPlan,
    SpecialistCall,
    WakeTarget,
    DeferredTarget,
    coordinate_plan,
)
from ai_quant_os.supervisor_sources import (
    SPECIALISTS,
    RegistryEvidence,
    SupervisorEvidence,
    effective_tools,
)

DRIFT_SPECIALISTS = (*SPECIALISTS, "A14")


def executable(agent_id: str, version: str) -> bool:
    return (agent_id == "A01" and version == "0.3.0") or (
        agent_id in DRIFT_SPECIALISTS and version == "0.2.0"
    )


class DriftRegistryEvidence(RegistryEvidence):
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
                raise ValueError("Unsupported drift supervisor execution catalog")
        return self


class DriftSupervisorEvidence(SupervisorEvidence):
    agent_registry: DriftRegistryEvidence


def read_context(context: CompiledContext) -> DriftSupervisorEvidence:
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A01",
        "0.3.0",
        "PAPER",
        "READY",
    ):
        raise ValueError("Drift supervisor requires its complete paper context")
    evidence = DriftSupervisorEvidence.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    world = evidence.world_state.world
    if world.as_of > context.created_at or context.valid_until > world.valid_until:
        raise ValueError("Supervisor world expiry mismatch")
    return evidence


def wake_plan(evidence: DriftSupervisorEvidence):
    selected, deferred = {}, []
    for row in evidence.agent_registry.entries:
        if (row.manifest.agent_id, row.manifest.agent_version) == ("A01", "0.3.0"):
            continue
        target = dict(
            agent_id=row.manifest.agent_id,
            agent_version=row.manifest.agent_version,
            manifest_ref=row.manifest.manifest_ref,
        )
        reason = (
            "QUARANTINED"
            if row.lifecycle == "QUARANTINED"
            else "PLANNED"
            if row.manifest.implementation_status == "PLANNED"
            else "UNSUPPORTED"
            if not row.executable
            else "HEALTH_ERROR"
            if evidence.health.status == "ERROR"
            else None
        )
        if reason:
            deferred.append(DeferredTarget(**target, reason=reason))
        else:
            if not effective_tools(row):
                raise ValueError("Specialist has no current read permissions")
            selected[row.manifest.agent_id] = WakeTarget(**target)
    return tuple(selected[agent] for agent in DRIFT_SPECIALISTS if agent in selected), tuple(
        deferred
    )


def child_run_id(context_ref: str, agent_id: str) -> str:
    if not context_ref.startswith("agent-context:") or agent_id not in DRIFT_SPECIALISTS:
        raise ValueError("Invalid drift supervisor child identity")
    return "supervisor_" + sha256(f"{context_ref}:{agent_id}:0.2.0".encode()).hexdigest()


class DriftSpecialistCall(SpecialistCall):
    @model_validator(mode="after")
    def receipt_claims(self):
        if (self.status == "COMPLETED") != (self.output_sha256 is not None):
            raise ValueError("Supervisor output claim/status mismatch")
        not_started = self.status.startswith("NOT_STARTED")
        if not_started != (self.context_ref is None) or not_started != (self.valid_until is None):
            raise ValueError("Supervisor receipt presence/status mismatch")
        if self.agent_id not in DRIFT_SPECIALISTS or self.agent_version != "0.2.0":
            raise ValueError("Drift supervisor cannot call this agent version")
        return self


class TaskPlan(LegacyTaskPlan):
    agent_version: Literal["0.3.0"] = "0.3.0"
    policy_version: Literal["paper-specialist-supervisor-v2"] = "paper-specialist-supervisor-v2"
    max_calls: Literal[7] = 7
    selected: tuple[WakeTarget, ...] = Field(max_length=7)
    calls: tuple[DriftSpecialistCall, ...] = Field(max_length=7)

    def require_context(self, context: CompiledContext) -> None:
        AgentEnvelope.require_context(self, context)
        evidence = read_context(context)
        selected, deferred = wake_plan(evidence)
        if (
            self.parent_context_ref,
            self.world_state_version,
            self.health_status,
            self.selected,
            self.deferred,
        ) != (
            context.context_ref,
            evidence.world_state.world.world_state_version,
            evidence.health.status,
            selected,
            deferred,
        ):
            raise ValueError("Drift supervisor plan does not match source evidence")

    @model_validator(mode="after")
    def exact_calls(self):
        identities = [(row.agent_id, row.agent_version, row.manifest_ref) for row in self.selected]
        if identities != [
            (row.agent_id, row.agent_version, row.manifest_ref) for row in self.calls
        ] or len(set(identities)) != len(identities):
            raise ValueError("Supervisor calls do not match wake plan")
        for row in self.calls:
            if row.run_id != child_run_id(self.parent_context_ref, row.agent_id):
                raise ValueError("Supervisor child identity mismatch")
            if row.status == "COMPLETED" and self.valid_until > row.valid_until:
                raise ValueError("Supervisor expiry exceeds specialist evidence")
        return self


async def coordinate_drift(invocation: AgentInvocation) -> TaskPlan:
    evidence = read_context(invocation.context)
    selected, deferred = wake_plan(evidence)
    return await coordinate_plan(
        invocation,
        evidence,
        selected,
        deferred,
        plan_type=TaskPlan,
        call_type=DriftSpecialistCall,
    )


def supervisor_drift_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A01", "0.3.0").manifest_ref, TaskPlan, coordinate_drift
    )

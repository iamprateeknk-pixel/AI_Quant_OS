"""A01 bounded specialist wake plan and archival call summaries; no capital authority."""

from hashlib import sha256
from decimal import Decimal
import json
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_ledger import GraphReceipt, RunStatus
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.supervisor_sources import SPECIALISTS, SupervisorEvidence, effective_tools


def child_run_id(context_ref: str, agent_id: str) -> str:
    if not context_ref.startswith("agent-context:") or agent_id not in SPECIALISTS:
        raise ValueError("Invalid supervisor child identity")
    return "supervisor_" + sha256(f"{context_ref}:{agent_id}:0.2.0".encode()).hexdigest()


class WakeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    agent_version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    manifest_ref: str = Field(pattern=r"^agent-manifest:[a-f0-9]{64}$")


class DeferredTarget(WakeTarget):
    reason: Literal["PLANNED", "UNSUPPORTED", "QUARANTINED", "HEALTH_ERROR"]


class SpecialistCall(WakeTarget):
    run_id: str = Field(pattern=r"^supervisor_[a-f0-9]{64}$")
    status: RunStatus | Literal["NOT_STARTED_PERMISSION", "NOT_STARTED_SOURCE"]
    context_ref: str | None = Field(default=None, pattern=r"^agent-context:[a-f0-9]{64}$")
    output_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    valid_until: AwareDatetime | None = None
    requires_escalation: Literal[True] = True

    @model_validator(mode="after")
    def receipt_claims(self):
        if (self.status == "COMPLETED") != (self.output_sha256 is not None):
            raise ValueError("Supervisor output claim/status mismatch")
        not_started = self.status.startswith("NOT_STARTED")
        if not_started != (self.context_ref is None) or not_started != (self.valid_until is None):
            raise ValueError("Supervisor receipt presence/status mismatch")
        if self.agent_id not in SPECIALISTS or self.agent_version != "0.2.0":
            raise ValueError("Supervisor cannot call this agent version")
        return self

    @classmethod
    def from_receipt(cls, receipt: GraphReceipt):
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


def read_supervisor_context(context: CompiledContext) -> SupervisorEvidence:
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A01",
        "0.2.0",
        "PAPER",
        "READY",
    ):
        raise ValueError("Supervisor requires its complete paper context")
    evidence = SupervisorEvidence.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    if (
        evidence.world_state.world.as_of > context.created_at
        or context.valid_until > evidence.world_state.world.valid_until
    ):
        raise ValueError("Supervisor world expiry mismatch")
    return evidence


def wake_plan(
    evidence: SupervisorEvidence,
) -> tuple[tuple[WakeTarget, ...], tuple[DeferredTarget, ...]]:
    selected, deferred = {}, []
    for row in evidence.agent_registry.entries:
        if (row.manifest.agent_id, row.manifest.agent_version) == ("A01", "0.2.0"):
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
    return tuple(selected[agent] for agent in SPECIALISTS if agent in selected), tuple(deferred)


class TaskPlan(AgentEnvelope):
    agent_id: Literal["A01"] = "A01"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["TaskPlan"] = "TaskPlan"
    policy_version: Literal["paper-specialist-supervisor-v1"] = "paper-specialist-supervisor-v1"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    hard_gate_owner: Literal["RiskKernel"] = "RiskKernel"
    max_calls: Literal[6] = 6
    dispatch_mode: Literal["SEQUENTIAL_PARENT_DEADLINE"] = "SEQUENTIAL_PARENT_DEADLINE"
    parent_context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")
    world_state_version: str = Field(pattern=r"^world:[a-f0-9]{64}$")
    health_status: Literal["UNAVAILABLE", "RESTORED_ONLY", "MONITORED", "ERROR"]
    selected: tuple[WakeTarget, ...] = Field(max_length=6)
    deferred: tuple[DeferredTarget, ...] = Field(max_length=128)
    calls: tuple[SpecialistCall, ...] = Field(max_length=6)

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        evidence = read_supervisor_context(context)
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
            raise ValueError("Supervisor plan does not match source evidence")

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


async def coordinate_specialists(invocation: AgentInvocation) -> TaskPlan:
    context = invocation.context
    evidence = read_supervisor_context(context)
    selected, deferred = wake_plan(evidence)
    return await coordinate_plan(invocation, evidence, selected, deferred)


async def coordinate_plan(
    invocation: AgentInvocation,
    evidence: SupervisorEvidence,
    selected: tuple[WakeTarget, ...],
    deferred: tuple[DeferredTarget, ...],
    *,
    plan_type=TaskPlan,
    call_type=SpecialistCall,
) -> TaskPlan:
    context = invocation.context
    result = await invocation.tools.call("TASK_ORCHESTRATE", {})
    if set(result) != {"calls"}:
        raise ValueError("Unexpected supervisor dispatch response")
    calls = tuple(call_type.model_validate(row) for row in result["calls"])
    expiry = min(
        (context.valid_until, *(row.valid_until for row in calls if row.status == "COMPLETED"))
    )
    contradictions = ["SPECIALIST_CALIBRATION_UNAVAILABLE", "REPUTATION_UNAVAILABLE"]
    if evidence.health.status != "MONITORED":
        contradictions.append("HEALTH_" + evidence.health.status)
    if evidence.world_state.world.status != "READY":
        contradictions.append("WORLD_" + evidence.world_state.world.status)
    contradictions.extend(
        f"{row.agent_id}_{row.status}" for row in calls if row.status != "COMPLETED"
    )
    return plan_type(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=expiry,
        contradictions=tuple(contradictions),
        trace_id=str(uuid4()),
        parent_context_ref=context.context_ref,
        world_state_version=evidence.world_state.world.world_state_version,
        health_status=evidence.health.status,
        selected=selected,
        deferred=deferred,
        calls=calls,
    )


def supervisor_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A01", "0.2.0").manifest_ref, TaskPlan, coordinate_specialists
    )

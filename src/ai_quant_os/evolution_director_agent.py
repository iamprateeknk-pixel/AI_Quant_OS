"""A17 withholds tournament plans until quality, cost, and promotion gates exist."""

from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import Field

from ai_quant_os.agent_context import CompiledContext, verify_context
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.evolution_director_sources import (
    AgentRegistryObservation,
    EvolutionEvidenceGap,
    INPUT_NAMES,
)
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation


class EvolutionPlan(AgentEnvelope):
    agent_id: Literal["A17"] = "A17"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["EvolutionPlan"] = "EvolutionPlan"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    state: Literal["WAIT_FOR_EVIDENCE"] = "WAIT_FOR_EVIDENCE"
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_count: int = Field(strict=True, ge=1, le=128)
    partial_mission_count: int = Field(strict=True, ge=0, le=128)
    planned_mission_count: int = Field(strict=True, ge=0, le=128)
    tournament_actions: tuple[str, ...] = Field(max_length=8)
    blockers: tuple[str, ...] = Field(min_length=1, max_length=16)
    plan_created: Literal[False] = False
    promotion_allowed: Literal[False] = False

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        inventory, blockers = _observations(context)
        if (
            self.state != "WAIT_FOR_EVIDENCE"
            or self.registry_sha256 != inventory.registry_sha256
            or self.manifest_count != inventory.manifest_count
            or self.partial_mission_count != inventory.partial_mission_count
            or self.planned_mission_count != inventory.planned_mission_count
            or self.tournament_actions
            or self.blockers != blockers
            or self.plan_created
        ):
            raise ValueError("Evolution output differs from current evidence")


def _observations(context: CompiledContext):
    verify_context(context)
    tools = ("ATTRIBUTION_READ", "HEALTH_READ", "ATTRIBUTION_READ", "HEALTH_READ", "HEALTH_READ")
    if (
        (context.agent_id, context.agent_version, context.scope, context.status)
        != ("A17", "0.2.0", "PAPER", "READY")
        or tuple(row.input_name for row in context.inputs) != INPUT_NAMES
        or tuple(row.source_tool for row in context.inputs) != tools
        or tuple(row.resource_class for row in context.inputs) != ("GENERAL",) * 5
    ):
        raise ValueError("Evolution director requires its exact protected paper context")
    gaps = tuple(
        EvolutionEvidenceGap.model_validate_json(row.payload_json) for row in context.inputs[:4]
    )
    inventory = AgentRegistryObservation.model_validate_json(context.inputs[4].payload_json)
    if (
        tuple(row.input_name for row in gaps) != INPUT_NAMES[:4]
        or any(
            row.as_of != source.as_of
            for row, source in zip((*gaps, inventory), context.inputs, strict=True)
        )
        or any(row.as_of > context.created_at for row in context.inputs)
        or tuple(row.source_id for row in context.inputs)
        != tuple(
            "evolution-director:" + name + ":" + sha256(row.payload_json.encode()).hexdigest()
            for name, row in zip(INPUT_NAMES, context.inputs, strict=True)
        )
    ):
        raise ValueError("Evolution evidence is stale or inconsistent")
    blockers = {
        "QUALITY_METRICS_UNAVAILABLE",
        "TRAINING_DATASETS_UNAVAILABLE",
        "COSTS_UNAVAILABLE",
        "TRACE_QUALITY_UNAVAILABLE",
        "INDEPENDENT_PROMOTION_GATE_UNAVAILABLE",
    }
    return inventory, tuple(sorted(blockers))


async def plan_evolution(invocation: AgentInvocation) -> EvolutionPlan:
    context = invocation.context
    inventory, blockers = _observations(context)
    return EvolutionPlan(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        contradictions=(),
        state="WAIT_FOR_EVIDENCE",
        registry_sha256=inventory.registry_sha256,
        manifest_count=inventory.manifest_count,
        partial_mission_count=inventory.partial_mission_count,
        planned_mission_count=inventory.planned_mission_count,
        tournament_actions=(),
        blockers=blockers,
    )


def evolution_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A17", "0.2.0").manifest_ref,
        EvolutionPlan,
        plan_evolution,
    )

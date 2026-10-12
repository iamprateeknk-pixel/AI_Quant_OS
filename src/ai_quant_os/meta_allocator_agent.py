"""A13 emits no allocation until strategy, covariance, and risk evidence are complete."""

from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import CompiledContext, verify_context
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.meta_allocator_sources import (
    CovarianceObservation,
    ExposureObservation,
    INPUT_NAMES,
    PortfolioObservation,
    RiskObservation,
    StrategySetObservation,
)


class AllocationWeight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_id: str = Field(min_length=1, max_length=128)
    weight: Decimal = Field(ge=0, le=1, allow_inf_nan=False)


class AllocationProposal(AgentEnvelope):
    agent_id: Literal["A13"] = "A13"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["AllocationProposal"] = "AllocationProposal"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    state: Literal["WAIT_FOR_EVIDENCE"] = "WAIT_FOR_EVIDENCE"
    portfolio_snapshot_id: str = Field(min_length=1, max_length=128)
    safety_state: Literal["NORMAL", "NO_NEW_RISK", "FLATTEN_ONLY", "HALT"]
    allocations: tuple[AllocationWeight, ...] = Field(max_length=100)
    blockers: tuple[str, ...] = Field(min_length=1, max_length=16)
    execution_authorized: Literal[False] = False
    promotion_allowed: Literal[False] = False

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        observations = _observations(context)
        if (
            self.state != "WAIT_FOR_EVIDENCE"
            or self.allocations
            or self.portfolio_snapshot_id != observations[1].snapshot_id
            or self.safety_state != observations[2].safety_state
            or self.blockers != _blockers(observations)
        ):
            raise ValueError("Allocation output differs from current portfolio evidence")


def _observations(context: CompiledContext):
    verify_context(context)
    tools = (
        "STRATEGY_REGISTRY_READ",
        "PORTFOLIO_READ",
        "PORTFOLIO_READ",
        "PORTFOLIO_READ",
        "PORTFOLIO_READ",
    )
    if (
        (context.agent_id, context.agent_version, context.scope, context.status)
        != ("A13", "0.2.0", "PAPER", "READY")
        or tuple(row.input_name for row in context.inputs) != INPUT_NAMES
        or tuple(row.source_tool for row in context.inputs) != tools
        or tuple(row.resource_class for row in context.inputs) != ("GENERAL",) * 5
    ):
        raise ValueError("Allocator requires its exact protected paper context")
    models = (
        StrategySetObservation,
        PortfolioObservation,
        RiskObservation,
        CovarianceObservation,
        ExposureObservation,
    )
    observations = tuple(
        model.model_validate_json(row.payload_json)
        for model, row in zip(models, context.inputs, strict=True)
    )
    if (
        tuple(row.source_id for row in context.inputs)
        != tuple(
            "meta-allocator:" + name + ":" + sha256(row.payload_json.encode()).hexdigest()
            for name, row in zip(INPUT_NAMES, context.inputs, strict=True)
        )
        or any(row.as_of > context.created_at for row in context.inputs)
        or any(
            observation.as_of != source.as_of
            for observation, source in zip(observations, context.inputs, strict=True)
        )
        or observations[0].availability != "UNAVAILABLE"
        or observations[1].coverage != "SINGLE_INSTRUMENT"
        or observations[2].risk_budget_status != "UNCALIBRATED"
        or observations[3].availability != "UNAVAILABLE"
        or observations[4].coverage != "SINGLE_INSTRUMENT"
        or observations[1].snapshot_id != observations[4].snapshot_id
    ):
        raise ValueError("Allocator observations are stale or inconsistent")
    return observations


def _blockers(observations) -> tuple[str, ...]:
    blockers = {
        "UPSTREAM_STRATEGY_SET_UNAVAILABLE",
        "PORTFOLIO_SNAPSHOT_INCOMPLETE",
        "RISK_BUDGET_UNCALIBRATED",
        "COVARIANCE_UNAVAILABLE",
        "PORTFOLIO_EXPOSURE_INCOMPLETE",
    }
    if observations[2].safety_state != "NORMAL":
        blockers.add("PAPER_SAFETY_STATE_RESTRICTS_ALLOCATION")
    return tuple(sorted(blockers))


async def propose_allocation(invocation: AgentInvocation) -> AllocationProposal:
    context = invocation.context
    observations = _observations(context)
    return AllocationProposal(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        contradictions=(),
        state="WAIT_FOR_EVIDENCE",
        portfolio_snapshot_id=observations[1].snapshot_id,
        safety_state=observations[2].safety_state,
        allocations=(),
        blockers=_blockers(observations),
    )


def allocator_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A13", "0.2.0").manifest_ref, AllocationProposal, propose_allocation
    )

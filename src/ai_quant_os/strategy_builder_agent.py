"""A09 renders an exact inert candidate through its task-bound publication tool."""

from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import Field

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.strategy_builder_sources import candidate_for_context
from ai_quant_os.strategy_contracts import StrategyGenome


class StrategyCandidate(AgentEnvelope):
    agent_id: Literal["A09"] = "A09"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["StrategyCandidate"] = "StrategyCandidate"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    review_ref: str = Field(pattern=r"^hypothesis-review:[a-f0-9]{64}$")
    genome: StrategyGenome
    tests_status: Literal["NOT_RUN"] = "NOT_RUN"
    isolation_verified: Literal[False] = False
    execution_authorized: Literal[False] = False
    promotion_authorized: Literal[False] = False
    runtime_registered: Literal[False] = False
    hard_gate_owner: Literal["ResearchPipeline"] = "ResearchPipeline"

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        candidate = candidate_for_context(context)
        if (
            self.candidate_ref,
            self.review_ref,
            self.genome,
            self.evidence_refs,
            self.created_at,
            self.valid_until,
            self.contradictions,
        ) != (
            candidate.candidate_ref,
            candidate.review.review_ref,
            candidate.genome,
            context.input_refs,
            context.created_at,
            context.valid_until,
            candidate.assumptions,
        ):
            raise ValueError("Candidate output differs from retained context recipe")


async def build_strategy(invocation: AgentInvocation) -> StrategyCandidate:
    context = invocation.context
    candidate = candidate_for_context(context)
    receipt = await invocation.tools.call("CANDIDATE_CREATE", {})
    if receipt != {"candidate_ref": candidate.candidate_ref, "status": "INERT_NOT_VALIDATED"}:
        raise ValueError("Candidate publication receipt mismatch")
    return StrategyCandidate(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        contradictions=candidate.assumptions,
        candidate_ref=candidate.candidate_ref,
        review_ref=candidate.review.review_ref,
        genome=candidate.genome,
    )


def builder_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A09", "0.2.0").manifest_ref, StrategyCandidate, build_strategy
    )

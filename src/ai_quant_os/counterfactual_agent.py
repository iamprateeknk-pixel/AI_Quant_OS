"""A15 reviews retained simulated alternatives without creating new replays."""

from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import CompiledContext, verify_context
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.counterfactual_sources import (
    CounterfactualReportObservation,
    INPUT_NAMES,
    UnavailableCounterfactualEvidence,
)
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation


class CounterfactualAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=64)
    pnl_difference: Decimal = Field(allow_inf_nan=False)
    interpretation: Literal["SIMULATED_ON_ALREADY_SEEN_DATA"] = "SIMULATED_ON_ALREADY_SEEN_DATA"


class CounterfactualComparison(AgentEnvelope):
    agent_id: Literal["A15"] = "A15"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["CounterfactualComparison"] = "CounterfactualComparison"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    state: Literal["DIAGNOSTIC_ONLY", "WAIT_FOR_EVIDENCE"]
    replay_id: str = Field(min_length=1, max_length=64)
    report_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    alternatives: tuple[CounterfactualAlternative, ...] = Field(max_length=7)
    blockers: tuple[str, ...] = Field(max_length=16)
    simulation_only: Literal[True] = True
    promotion_allowed: Literal[False] = False

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        observation, blockers, state = _comparison(context)
        expected = tuple(
            CounterfactualAlternative(name=row.name, pnl_difference=row.pnl_difference)
            for row in observation.variants
        )
        if (
            self.replay_id != context.task_id
            or self.report_sha256 != observation.report_sha256
            or self.alternatives != expected
            or self.blockers != blockers
            or self.state != state
        ):
            raise ValueError("Counterfactual output differs from retained replay evidence")


def _comparison(context: CompiledContext):
    verify_context(context)
    tools = ("ATTRIBUTION_READ", "HISTORY_READ", "PORTFOLIO_READ", "HISTORY_READ")
    if (
        (context.agent_id, context.agent_version, context.scope, context.status)
        != ("A15", "0.2.0", "PAPER", "READY")
        or tuple(row.input_name for row in context.inputs) != INPUT_NAMES
        or tuple(row.source_tool for row in context.inputs) != tools
        or tuple(row.resource_class for row in context.inputs) != ("GENERAL",) * 4
    ):
        raise ValueError("Counterfactual reviewer requires its exact paper context")
    observation = CounterfactualReportObservation.model_validate_json(
        context.inputs[0].payload_json
    )
    missing = tuple(
        UnavailableCounterfactualEvidence.model_validate_json(row.payload_json)
        for row in context.inputs[1:]
    )
    if (
        observation.replay_id != context.task_id
        or any(row.as_of > context.created_at for row in context.inputs)
        or any(
            row.as_of != source.as_of
            for row, source in zip((observation, *missing), context.inputs, strict=True)
        )
        or tuple(row.source_id for row in context.inputs)
        != tuple(
            "counterfactual-review:" + name + ":" + sha256(row.payload_json.encode()).hexdigest()
            for name, row in zip(INPUT_NAMES, context.inputs, strict=True)
        )
        or tuple(row.input_name for row in missing) != INPUT_NAMES[1:]
    ):
        raise ValueError("Counterfactual evidence is stale or inconsistent")
    blockers = {
        "SIMULATED_NOT_OBSERVED",
        "DECISION_LINEAGE_UNAVAILABLE",
        "FILL_LINEAGE_UNAVAILABLE",
        "WORLD_STATE_ARCHIVE_UNAVAILABLE",
    }
    if not observation.available:
        blockers.add("COUNTERFACTUAL_REPORT_UNAVAILABLE")
    if observation.outcome != "COMPLETED":
        blockers.add("COUNTERFACTUAL_REPLAY_NOT_COMPLETED")
    state = (
        "DIAGNOSTIC_ONLY"
        if observation.available and observation.outcome == "COMPLETED"
        else "WAIT_FOR_EVIDENCE"
    )
    return observation, tuple(sorted(blockers)), state


async def compare_counterfactuals(invocation: AgentInvocation) -> CounterfactualComparison:
    context = invocation.context
    observation, blockers, state = _comparison(context)
    return CounterfactualComparison(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        contradictions=(),
        state=state,
        replay_id=observation.replay_id,
        report_sha256=observation.report_sha256,
        alternatives=tuple(
            CounterfactualAlternative(name=row.name, pnl_difference=row.pnl_difference)
            for row in observation.variants
        ),
        blockers=blockers,
    )


def counterfactual_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A15", "0.2.0").manifest_ref,
        CounterfactualComparison,
        compare_counterfactuals,
    )

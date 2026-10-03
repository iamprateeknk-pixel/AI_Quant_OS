"""A08 deterministic prospective hypothesis generation from bound development facts."""

from uuid import uuid4

from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.research_hypothesis_contracts import HypothesisSpec, hypothesis_recipe


async def generate_hypotheses(invocation: AgentInvocation) -> HypothesisSpec:
    context = invocation.context
    return HypothesisSpec(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        **hypothesis_recipe(context),
    )


def hypothesis_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A08", "0.2.0").manifest_ref, HypothesisSpec, generate_hypotheses
    )

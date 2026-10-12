"""A17 must not plan tournaments from uncalibrated, disconnected evidence."""

import asyncio
from datetime import datetime, timezone

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_director_sources import evolution_sources

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def context_for():
    registry = load_agent_catalog()
    return registry, asyncio.run(
        ContextCompiler(
            registry,
            evolution_sources(registry, now=NOW),
            clock=lambda: NOW,
        ).compile("A17", "0.2.0", task_id="evolution-review", scope="PAPER")
    )


def test_a17_withholds_evolution_plan_without_quality_and_promotion_evidence():
    from ai_quant_os.evolution_director_agent import evolution_implementation
    from ai_quant_os.graph_runtime import AgentInvocation

    registry, context = context_for()
    output = asyncio.run(
        evolution_implementation(registry).invoke(AgentInvocation(context, tools=None))
    )
    output.require_context(context)
    assert output.state == "WAIT_FOR_EVIDENCE"
    assert output.plan_created is False
    assert output.tournament_actions == ()
    assert "QUALITY_METRICS_UNAVAILABLE" in output.blockers
    assert "TRAINING_DATASETS_UNAVAILABLE" in output.blockers
    assert "INDEPENDENT_PROMOTION_GATE_UNAVAILABLE" in output.blockers
    assert output.promotion_allowed is False

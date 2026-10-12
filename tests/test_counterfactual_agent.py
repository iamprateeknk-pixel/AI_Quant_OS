"""A15 keeps stored replay sensitivity separate from observed market facts."""

import asyncio
from datetime import datetime, timezone

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.counterfactual_sources import counterfactual_sources

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def context_for():
    registry = load_agent_catalog()
    return registry, asyncio.run(
        ContextCompiler(
            registry,
            counterfactual_sources(None, replay_id="missing-replay", now=NOW),
            clock=lambda: NOW,
        ).compile("A15", "0.2.0", task_id="missing-replay", scope="PAPER")
    )


def test_a15_withholds_diagnostic_comparison_without_a_retained_replay():
    from ai_quant_os.counterfactual_agent import counterfactual_implementation
    from ai_quant_os.graph_runtime import AgentInvocation

    registry, context = context_for()
    output = asyncio.run(
        counterfactual_implementation(registry).invoke(AgentInvocation(context, tools=None))
    )
    output.require_context(context)
    assert output.state == "WAIT_FOR_EVIDENCE"
    assert output.alternatives == ()
    assert "COUNTERFACTUAL_REPORT_UNAVAILABLE" in output.blockers
    assert output.simulation_only is True
    assert output.promotion_allowed is False

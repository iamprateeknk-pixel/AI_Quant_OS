"""A13 cannot propose allocations without connected upstream evidence."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.meta_allocator_sources import AllocationInputs, allocator_sources
from ai_quant_os.portfolio import PortfolioExposure
from ai_quant_os.risk import RiskLimits, SafetyState

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def context_for(*, context_time=NOW, safety_state=SafetyState.NORMAL.value):
    registry = load_agent_catalog()
    sources = allocator_sources(
        AllocationInputs(
            portfolio=PortfolioExposure.empty(),
            risk_limits=RiskLimits(Decimal(10), Decimal(20), 5),
            safety_state=safety_state,
        ),
        now=NOW,
    )
    return registry, asyncio.run(
        ContextCompiler(registry, sources, clock=lambda: context_time).compile(
            "A13", "0.2.0", task_id="allocator-review", scope="PAPER"
        )
    )


def test_allocator_withholds_allocation_when_selector_and_covariance_are_unavailable():
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.meta_allocator_agent import allocator_implementation

    registry, context = context_for()
    output = asyncio.run(
        allocator_implementation(registry).invoke(AgentInvocation(context, tools=None))
    )
    output.require_context(context)
    assert output.state == "WAIT_FOR_EVIDENCE"
    assert output.allocations == ()
    assert "UPSTREAM_STRATEGY_SET_UNAVAILABLE" in output.blockers
    assert "COVARIANCE_UNAVAILABLE" in output.blockers
    assert output.confidence == 0 and output.uncertainty == 1
    assert output.execution_authorized is False
    assert output.safety_state == "NORMAL"


def test_allocator_rejects_input_substitution():
    import pytest

    from ai_quant_os.meta_allocator_agent import _observations

    _, context = context_for()
    changed = context.model_copy(update={"task_id": "different-task"})
    with pytest.raises(ValueError):
        _observations(changed)


def test_allocator_accepts_source_observations_captured_before_context_assembly():
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.meta_allocator_agent import allocator_implementation

    registry, context = context_for(context_time=NOW + timedelta(microseconds=1))
    output = asyncio.run(
        allocator_implementation(registry).invoke(AgentInvocation(context, tools=None))
    )
    output.require_context(context)


def test_allocator_surfaces_restrictive_paper_safety_state():
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.meta_allocator_agent import allocator_implementation

    registry, context = context_for(safety_state=SafetyState.HALT.value)
    output = asyncio.run(
        allocator_implementation(registry).invoke(AgentInvocation(context, tools=None))
    )
    assert output.safety_state == "HALT"
    assert "PAPER_SAFETY_STATE_RESTRICTS_ALLOCATION" in output.blockers

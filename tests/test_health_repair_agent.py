"""A16 withholds repair proposals until diagnosis and regression evidence are linked."""

import asyncio
from datetime import datetime, timezone

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.health_repair_sources import RepairInputs, repair_sources

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def context_for():
    registry = load_agent_catalog()
    sources = repair_sources(
        RepairInputs(health_status="UNAVAILABLE", open_incidents=None), now=NOW
    )
    return registry, asyncio.run(
        ContextCompiler(registry, sources, clock=lambda: NOW).compile(
            "A16", "0.2.0", task_id="repair-review", scope="PAPER"
        )
    )


def test_a16_withholds_repair_candidate_without_traces_tests_and_incident_details():
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.health_repair_agent import repair_implementation

    registry, context = context_for()
    assert context.status == "READY", [
        (row.input_name, row.status, row.source_tool) for row in context.inputs
    ]
    output = asyncio.run(
        repair_implementation(registry).invoke(AgentInvocation(context, tools=None))
    )
    output.require_context(context)
    assert output.state == "WAIT_FOR_EVIDENCE"
    assert output.repair_candidate_created is False
    assert output.repair_plan == ()
    assert "HEALTH_EVIDENCE_UNAVAILABLE" in output.blockers
    assert "TRACE_EVIDENCE_UNAVAILABLE" in output.blockers
    assert "CODE_TEST_EVIDENCE_UNAVAILABLE" in output.blockers
    assert output.promotion_allowed is False

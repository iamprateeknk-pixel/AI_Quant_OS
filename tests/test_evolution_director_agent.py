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


def test_a17_receives_durable_operational_quality_but_still_requires_calibration():
    from ai_quant_os.agent_quality import AgentOperationalQualityObservation
    from ai_quant_os.evolution_director_agent import evolution_implementation
    from ai_quant_os.graph_ledger import GraphReceipt
    from ai_quant_os.graph_runtime import AgentInvocation

    registry = load_agent_catalog()
    manifest = registry.get("A02", "0.2.0")
    receipt = GraphReceipt(
        run_id="observed-run",
        agent_id=manifest.agent_id,
        agent_version=manifest.agent_version,
        task_id="regime-task",
        context_ref="context-ref",
        manifest_ref=manifest.manifest_ref,
        input_refs=(),
        created_at=NOW,
        valid_until=NOW.replace(second=5),
        status="COMPLETED",
        output_type=manifest.output_type,
        output_json="{}",
    )
    context = asyncio.run(
        ContextCompiler(
            registry,
            evolution_sources(registry, now=NOW, quality_receipts=(receipt,)),
            clock=lambda: NOW,
        ).compile("A17", "0.2.0", task_id="evolution-quality", scope="PAPER")
    )
    quality = AgentOperationalQualityObservation.model_validate_json(context.inputs[0].payload_json)
    assert quality.availability == "AVAILABLE"
    assert quality.rows[0].status_counts["COMPLETED"] == 1
    output = asyncio.run(
        evolution_implementation(registry).invoke(AgentInvocation(context, tools=None))
    )
    output.require_context(context)
    assert "QUALITY_METRICS_UNAVAILABLE" not in output.blockers
    assert "QUALITY_CALIBRATION_UNAVAILABLE" in output.blockers
    assert output.promotion_allowed is False

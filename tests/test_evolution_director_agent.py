"""A17 must not plan tournaments from uncalibrated, disconnected evidence."""

import asyncio
from datetime import datetime, timezone
from hashlib import sha256

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
    from ai_quant_os.graph_trace import GraphTrace, GraphTraceEvent, trace_identity
    from ai_quant_os.graph_runtime import AgentInvocation

    registry = load_agent_catalog()
    manifest = registry.get("A02", "0.2.0")
    receipt = GraphReceipt(
        run_id="observed-run",
        agent_id=manifest.agent_id,
        agent_version=manifest.agent_version,
        task_id="regime-task",
        context_ref="agent-context:" + sha256(b"quality-context").hexdigest(),
        manifest_ref=manifest.manifest_ref,
        input_refs=(),
        created_at=NOW,
        valid_until=NOW.replace(second=5),
        status="COMPLETED",
        output_type=manifest.output_type,
        output_json="{}",
    )
    start = NOW.replace(second=1)
    finish = NOW.replace(second=3)
    events = tuple(
        GraphTraceEvent(
            sequence=sequence,
            status=status,
            record_hash=sha256(f"{sequence}:record".encode()).hexdigest(),
            event_hash=sha256(f"{sequence}:event".encode()).hexdigest(),
            observed_at=observed_at,
        )
        for sequence, status, observed_at in (
            (1, "CREATED", NOW),
            (2, "RUNNING", start),
            (3, "COMPLETED", finish),
        )
    )
    trace = GraphTrace(
        scope="PAPER",
        trace_id=trace_identity(receipt.run_id, receipt.context_ref, receipt.manifest_ref),
        run_id=receipt.run_id,
        task_id=receipt.task_id,
        agent_id=receipt.agent_id,
        agent_version=receipt.agent_version,
        context_ref=receipt.context_ref,
        manifest_ref=receipt.manifest_ref,
        input_refs=receipt.input_refs,
        context_observed_at=NOW,
        context_valid_until=receipt.valid_until,
        status="COMPLETED",
        output_sha256=sha256(receipt.output_json.encode()).hexdigest(),
        events=events,
        timing_state="AVAILABLE",
        elapsed_seconds=2,
    )
    context = asyncio.run(
        ContextCompiler(
            registry,
            evolution_sources(
                registry,
                now=NOW,
                quality_receipts=(receipt,),
                quality_traces=(trace,),
            ),
            clock=lambda: NOW,
        ).compile("A17", "0.2.0", task_id="evolution-quality", scope="PAPER")
    )
    quality = AgentOperationalQualityObservation.model_validate_json(context.inputs[0].payload_json)
    assert quality.availability == "AVAILABLE"
    assert quality.rows[0].status_counts["COMPLETED"] == 1
    assert quality.rows[0].mean_duration_seconds == 2
    assert quality.rows[0].p95_duration_seconds == 2
    output = asyncio.run(
        evolution_implementation(registry).invoke(AgentInvocation(context, tools=None))
    )
    output.require_context(context)
    assert "QUALITY_METRICS_UNAVAILABLE" not in output.blockers
    assert "QUALITY_CALIBRATION_UNAVAILABLE" in output.blockers
    assert output.promotion_allowed is False

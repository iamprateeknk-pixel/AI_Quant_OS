"""A16 withholds repair proposals until diagnosis and regression evidence are linked."""

import asyncio
from datetime import datetime, timezone

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.health_contracts import HealthIncident
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


def test_a16_reports_bounded_incident_identity_without_raw_evidence():
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.health_repair_agent import repair_implementation

    incident = HealthIncident(
        code="AGENT_FAILED",
        subject="run-123",
        evidence_json='{"private":"do-not-copy"}',
        detected_at=NOW,
        agent_id="A02",
        agent_version="0.2.0",
        manifest_ref="sha256:manifest",
    )
    registry = load_agent_catalog()
    sources = repair_sources(
        RepairInputs(health_status="MONITORED", open_incidents=1, incidents=(incident,)),
        now=NOW,
        include_incidents=True,
    )
    context = asyncio.run(
        ContextCompiler(registry, sources, clock=lambda: NOW).compile(
            "A16", "0.3.0", task_id="repair-details", scope="PAPER"
        )
    )

    output = asyncio.run(
        repair_implementation(registry, "0.3.0").invoke(AgentInvocation(context, tools=None))
    )

    assert output.incident_ids == (incident.incident_id,)
    assert output.incident_context_state == "DETAILS"
    assert "INCIDENT_DETAILS_UNAVAILABLE" not in output.blockers
    assert "do-not-copy" not in context.inputs[3].payload_json
    assert output.state == "WAIT_FOR_EVIDENCE"
    assert output.repair_candidate_created is False
    assert output.promotion_allowed is False


def test_detailed_incident_context_caps_entries_and_marks_truncation():
    from ai_quant_os.health_repair_sources import DetailedIncidentContextObservation

    incidents = tuple(
        HealthIncident("QUOTE_STALE", f"feed-{index}", "{}", NOW) for index in range(10)
    )
    sources = repair_sources(
        RepairInputs("MONITORED", len(incidents), incidents),
        now=NOW,
        include_incidents=True,
    )
    record = asyncio.run(sources["incident_context"].fetch())
    observation = DetailedIncidentContextObservation.model_validate_json(record.payload_json)

    assert observation.availability == "TRUNCATED"
    assert observation.total_incidents == 10
    assert len(observation.incidents) == 8
    assert all("subject" not in item.model_dump() for item in observation.incidents)


def test_a16_v4_only_counts_a_failure_trace_that_matches_its_incident_pins():
    from ai_quant_os.health_repair_sources import RepairTrace

    incident = HealthIncident(
        "AGENT_FAILED",
        "run-123",
        "{}",
        NOW,
        agent_id="A02",
        agent_version="0.2.0",
        manifest_ref="agent-manifest:" + "a" * 64,
    )
    trace = RepairTrace(
        incident_id=incident.incident_id,
        trace_id="graph-trace:" + "b" * 64,
        scope="PAPER",
        context_ref="agent-context:" + "c" * 64,
        agent_id="A02",
        agent_version="0.2.0",
        manifest_ref=incident.manifest_ref,
        status="FAILED",
        event_hashes=("d" * 64, "e" * 64, "f" * 64),
        output_sha256=None,
    )
    sources = repair_sources(
        RepairInputs("MONITORED", 1, (incident,), (trace,)),
        now=NOW,
        include_incidents=True,
        include_traces=True,
    )
    record = asyncio.run(sources["traces"].fetch())
    from ai_quant_os.health_repair_sources import TraceEvidenceObservation

    observation = TraceEvidenceObservation.model_validate_json(record.payload_json)

    assert observation.availability == "AVAILABLE"
    assert observation.traces[0].trace_id == trace.trace_id

    registry = load_agent_catalog()
    context = asyncio.run(
        ContextCompiler(
            registry,
            sources,
            clock=lambda: NOW,
        ).compile("A16", "0.4.0", task_id="repair-trace", scope="PAPER")
    )
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.health_repair_agent import repair_implementation

    output = asyncio.run(
        repair_implementation(registry, "0.4.0").invoke(AgentInvocation(context, tools=None))
    )
    assert output.trace_context_state == "AVAILABLE"
    assert output.trace_refs[0].incident_id == incident.incident_id
    assert output.trace_refs[0].trace_id == trace.trace_id
    assert "TRACE_EVIDENCE_UNAVAILABLE" not in output.blockers
    assert "CODE_TEST_EVIDENCE_UNAVAILABLE" in output.blockers
    assert output.state == "WAIT_FOR_EVIDENCE"


def test_a16_v4_rejects_trace_with_a_different_agent_manifest():
    import pytest

    from ai_quant_os.health_repair_sources import RepairTrace

    incident = HealthIncident(
        "AGENT_FAILED",
        "run-123",
        "{}",
        NOW,
        agent_id="A02",
        agent_version="0.2.0",
        manifest_ref="agent-manifest:" + "a" * 64,
    )
    trace = RepairTrace(
        incident_id=incident.incident_id,
        trace_id="graph-trace:" + "b" * 64,
        scope="PAPER",
        context_ref="agent-context:" + "c" * 64,
        agent_id="A02",
        agent_version="0.2.0",
        manifest_ref="agent-manifest:" + "9" * 64,
        status="FAILED",
        event_hashes=("d" * 64, "e" * 64, "f" * 64),
        output_sha256=None,
    )

    with pytest.raises(ValueError, match="trace does not match incident"):
        repair_sources(
            RepairInputs("MONITORED", 1, (incident,), (trace,)),
            now=NOW,
            include_incidents=True,
            include_traces=True,
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

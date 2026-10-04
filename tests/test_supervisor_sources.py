import asyncio
from datetime import datetime, timezone

import pytest

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.supervisor_agent import read_supervisor_context, wake_plan
from ai_quant_os.supervisor_sources import SupervisorEvidence, supervisor_sources, SPECIALISTS
from ai_quant_os.world_state import WorldStateEngine
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def context(registry=None, **health):
    registry = registry or load_agent_catalog()
    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    return asyncio.run(
        ContextCompiler(
            registry,
            supervisor_sources(world, registry, **health),
            clock=lambda: NOW,
            max_input_bytes=65536,
            max_context_bytes=160000,
        ).compile("A01", "0.2.0", task_id="plan", scope="PAPER")
    )


def test_wake_plan_covers_real_supported_versions_with_exact_permissions():
    evidence = read_supervisor_context(context())
    selected, deferred = wake_plan(evidence)
    assert tuple(row.agent_id for row in selected) == SPECIALISTS
    assert len(deferred) == 21
    assert sum(row.reason == "UNSUPPORTED" for row in deferred) == 3
    assert sum(row.reason == "PLANNED" for row in deferred) == 18
    assert any(
        row.agent_id == "A18" and row.agent_version == "0.2.0" and row.reason == "UNSUPPORTED"
        for row in deferred
    )
    assert evidence.health.status == "UNAVAILABLE" and evidence.health.open_incidents is None


def test_quarantine_and_health_error_withhold_calls_without_clearance():
    registry = load_agent_catalog()
    registry.quarantine("A03", "0.2.0", registry.get("A03", "0.2.0").manifest_ref)
    selected, deferred = wake_plan(read_supervisor_context(context(registry)))
    assert tuple(row.agent_id for row in selected) == tuple(x for x in SPECIALISTS if x != "A03")
    assert any(row.agent_id == "A03" and row.reason == "QUARANTINED" for row in deferred)
    selected, deferred = wake_plan(
        read_supervisor_context(context(registry, health_status="ERROR", open_incidents=1))
    )
    assert selected == () and len(deferred) == 27
    assert registry.lifecycle("A03", "0.2.0") == "QUARANTINED"


@pytest.mark.parametrize("field", ["permissions", "health", "agent_registry"])
def test_evidence_rejects_disagreeing_world_observation(field):
    raw = read_supervisor_context(context()).model_dump(mode="json")
    raw[field]["world_state_version"] = "world:" + "0" * 64
    with pytest.raises(ValueError, match="different observations"):
        SupervisorEvidence.model_validate(raw)


@pytest.mark.parametrize("mutation", ["grant", "duplicate", "fake_executable", "missing_count"])
def test_evidence_rejects_forged_permission_catalog_and_health_claims(mutation):
    raw = read_supervisor_context(context()).model_dump(mode="json")
    if mutation == "grant":
        raw["permissions"]["entries"][0]["tools"].append("BROKER_SUBMIT")
    elif mutation == "duplicate":
        raw["agent_registry"]["entries"].append(raw["agent_registry"]["entries"][0])
    elif mutation == "fake_executable":
        raw["agent_registry"]["entries"][0]["executable"] = True
    else:
        raw["health"]["status"] = "MONITORED"
    with pytest.raises(ValueError):
        SupervisorEvidence.model_validate(raw)

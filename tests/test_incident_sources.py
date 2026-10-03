import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.incident_agent import (
    IncidentRecommendation,
    proposal_for_context,
    read_incident_context,
)
from ai_quant_os.incident_sources import incident_sources, reconciliation_evidence
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.portfolio import PaperFill
from ai_quant_os.risk import SafetyState
from ai_quant_os.world_state import WorldStateEngine
from test_incident_api import NOW


def sources(*, incidents=None, live=SafetyState.NORMAL, durable=SafetyState.NORMAL):
    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    return incident_sources(
        world,
        incidents=incidents,
        watchdog_status="UNAVAILABLE" if incidents is None else "RESTORED_ONLY",
        broker={
            "broker_id": "PAPER",
            "enabled": True,
            "connection_state": "CONNECTED",
            "instruments": ["EURJPY", "EURUSD", "GBPUSD", "USDJPY"],
        },
        orders=(),
        local_fills=(),
        broker_fills=(),
        live_state=live,
        durable_state=durable,
    )


async def compile_context(
    *, task="review", incidents=None, live=SafetyState.NORMAL, durable=SafetyState.NORMAL
):
    registry = load_agent_catalog()
    return await ContextCompiler(
        registry,
        sources(incidents=incidents, live=live, durable=durable),
        clock=lambda: NOW,
        max_input_bytes=65536,
        max_context_bytes=160000,
    ).compile("A18", "0.2.0", task_id=task, scope="PAPER")


def test_full_thousand_incident_collection_is_summarized_without_dropping_members():
    incidents = tuple(HealthIncident("QUOTE_STALE", f"feed-{i}", "{}", NOW) for i in range(1000))
    context = asyncio.run(compile_context(incidents=incidents))
    evidence = read_incident_context(context)
    assert context.status == "READY"
    assert evidence.watchdog_events.incident_count == 1000
    assert evidence.watchdog_events.code_counts["QUOTE_STALE"] == 1000
    assert sum(evidence.watchdog_events.code_counts.values()) == 1000
    assert "QUOTE_STALE" in proposal_for_context(context).findings


def test_duplicate_incidents_and_overflow_fail_before_publication():
    incident = HealthIncident("QUOTE_STALE", "feed", "{}", NOW)
    with pytest.raises(ValueError, match="Duplicate"):
        sources(incidents=(incident, incident))
    with pytest.raises(ValueError, match="bound"):
        sources(incidents=(incident,) * 1001)


def test_reconciliation_compares_complete_identity_without_source_mutation():
    fill = PaperFill(
        "fill",
        "order",
        "EURUSD",
        "BUY",
        Decimal(1),
        Decimal("1.1"),
        Decimal(0),
        NOW,
        "PAPER_BROKER",
    )
    assert reconciliation_evidence((fill,), (fill,), now=NOW).matches
    changed = replace(fill, price=Decimal("1.2"))
    report = reconciliation_evidence((fill,), (changed,), now=NOW)
    assert not report.matches and report.conflicting_count == 1
    assert fill.price == Decimal("1.1")
    assert reconciliation_evidence((fill,), (), now=NOW).local_only_count == 1
    with pytest.raises(ValueError, match="Duplicate"):
        reconciliation_evidence((fill, fill), (), now=NOW)


def test_future_retained_observation_and_mismatched_control_states_escalate():
    future = HealthIncident("QUOTE_STALE", "future", "{}", NOW + timedelta(seconds=1))
    proposal = proposal_for_context(asyncio.run(compile_context(incidents=(future,))))
    assert proposal.recommended_state == SafetyState.HALT
    assert "FUTURE_LEDGER_OBSERVATION" in proposal.findings
    proposal = proposal_for_context(asyncio.run(compile_context(live=SafetyState.NO_NEW_RISK)))
    assert (
        proposal.recommended_state == SafetyState.HALT
        and "RISK_STATE_MISMATCH" in proposal.findings
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"recommended_state": "NORMAL"},
        {"watchdog_incident_count": 0},
        {"findings": ()},
        {"proposal_ref": "incident-proposal:" + "0" * 64},
    ],
)
def test_incident_output_cannot_forge_retained_source_claims(changes):
    context = asyncio.run(compile_context())
    proposal = proposal_for_context(context)
    output = IncidentRecommendation(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        contradictions=proposal.findings,
        trace_id="checked-fixture",
        proposal_ref=proposal.proposal_ref,
        incident_class=proposal.incident_class,
        recommended_state=proposal.recommended_state,
        findings=proposal.findings,
        workflow=proposal.workflow,
        watchdog_incident_count=proposal.watchdog_incident_count,
    )
    output.require_context(context)
    forged = IncidentRecommendation.model_validate({**output.model_dump(), **changes})
    with pytest.raises(ValueError, match="retained source"):
        forged.require_context(context)

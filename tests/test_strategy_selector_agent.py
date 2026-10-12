"""A12 must withhold eligibility while approved-candidate evidence is unavailable."""

import asyncio
from datetime import datetime, timezone

import pytest


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def context_for(candidates=()):
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.strategy_selector_sources import selector_sources
    from ai_quant_os.world_state import WorldStateEngine

    registry = load_agent_catalog()
    snapshot = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    sources = selector_sources(candidates, snapshot, now=NOW, registry_available=True)
    return registry, asyncio.run(
        ContextCompiler(registry, sources, clock=lambda: NOW).compile(
            "A12", "0.2.0", task_id="selector-review", scope="PAPER"
        )
    )


def test_selector_returns_empty_advisory_set_when_inputs_do_not_support_eligibility():
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.strategy_selector_agent import selector_implementation

    registry, context = context_for()

    class Tools:
        async def call(self, tool, payload):
            assert tool == "STRATEGY_REGISTRY_READ"
            return {
                "registry_sha256": payload["registry_sha256"],
                "candidate_count": 0,
                "status": "CHECKED_REGISTRY_SNAPSHOT",
            }

    output = asyncio.run(
        selector_implementation(registry).invoke(AgentInvocation(context, Tools()))
    )
    output.require_context(context)
    assert output.eligible_strategies == ()
    assert output.state == "WAIT_FOR_EVIDENCE"
    assert "NO_APPROVED_CANDIDATES" in output.blockers
    assert "CANDIDATE_PERFORMANCE_UNCONNECTED" in output.blockers
    assert "CANDIDATE_DRIFT_UNASSESSED" in output.blockers
    assert output.confidence == 0 and output.uncertainty == 1
    assert output.promotion_allowed is False


def test_selector_never_ranks_a_challenger_or_accepts_a_changed_registry_receipt():
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.research_candidate_store import CandidateView
    from ai_quant_os.research_candidates import CandidateManifest
    from ai_quant_os.strategy_selector_agent import selector_implementation

    manifest = CandidateManifest(
        schema_version="1.0",
        candidate_id="a" * 64,
        experiment_id="experiment-one",
        hypothesis_id="hypothesis-one",
        report_sha256="b" * 64,
        plan_sha256="c" * 64,
        selected_genome=None,
        research_outcome="VALIDATION_INCOMPLETE",
        blocking_gates=("ADVERSARIAL", "INDEPENDENT_REVIEW", "SHADOW"),
        reasons=("ADVERSARIAL:PENDING",),
        promotion_allowed=False,
    )
    view = CandidateView(manifest, 1, "CHALLENGER", "d" * 64)
    registry, context = context_for((view,))

    class Tools:
        async def call(self, tool, payload):
            return {
                "registry_sha256": "0" * 64,
                "candidate_count": 1,
                "status": "CHECKED_REGISTRY_SNAPSHOT",
            }

    with pytest.raises(ValueError, match="registry receipt"):
        asyncio.run(selector_implementation(registry).invoke(AgentInvocation(context, Tools())))

"""A12 must withhold eligibility while approved-candidate evidence is unavailable."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def context_for(candidates=(), *, context_time=NOW, agent_version="0.2.0", shadow_store=None):
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.strategy_selector_sources import selector_sources, selector_sources_v3
    from ai_quant_os.world_state import WorldStateEngine

    registry = load_agent_catalog()
    snapshot = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    source_builder = selector_sources if agent_version == "0.2.0" else selector_sources_v3
    sources = source_builder(
        candidates,
        snapshot,
        now=NOW,
        registry_available=True,
        **({"shadow_store": shadow_store} if agent_version == "0.3.0" else {}),
    )
    return registry, asyncio.run(
        ContextCompiler(registry, sources, clock=lambda: context_time).compile(
            "A12", agent_version, task_id="selector-review", scope="PAPER"
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


def test_selector_accepts_source_observations_captured_before_context_assembly():
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.strategy_selector_agent import selector_implementation

    registry, context = context_for(context_time=NOW + timedelta(microseconds=1))

    class Tools:
        async def call(self, tool, payload):
            return {
                "registry_sha256": payload["registry_sha256"],
                "candidate_count": 0,
                "status": "CHECKED_REGISTRY_SNAPSHOT",
            }

    output = asyncio.run(
        selector_implementation(registry).invoke(AgentInvocation(context, Tools()))
    )
    output.require_context(context)


def test_selector_v3_retains_forward_shadow_metrics_without_ranking_candidates(tmp_path):
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.shadow_store import ShadowStore
    from ai_quant_os.strategy_selector_agent import selector_implementation_v3

    registry, context = context_for(
        agent_version="0.3.0", shadow_store=ShadowStore(tmp_path / "shadow.sqlite3")
    )

    class Tools:
        async def call(self, tool, payload):
            assert tool == "STRATEGY_REGISTRY_READ"
            return {
                "registry_sha256": payload["registry_sha256"],
                "candidate_count": 0,
                "performance_sha256": payload["performance_sha256"],
                "status": "CHECKED_REGISTRY_AND_SHADOW_SNAPSHOT",
            }

    output = asyncio.run(
        selector_implementation_v3(registry).invoke(AgentInvocation(context, Tools()))
    )
    output.require_context(context)
    assert output.agent_version == "0.3.0"
    assert output.eligible_strategies == ()
    assert "NO_APPROVED_CANDIDATES" in output.blockers
    assert "CANDIDATE_SHADOW_PERFORMANCE_MISSING" in output.blockers
    assert "CANDIDATE_DRIFT_UNASSESSED" in output.blockers
    assert output.promotion_allowed is False


def test_selector_shadow_history_scan_respects_context_deadline_without_blocking_loop(
    tmp_path, monkeypatch
):
    import threading
    import time

    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.shadow_store import ShadowStore
    from ai_quant_os.strategy_selector_sources import selector_sources_v3
    from ai_quant_os.world_state import WorldStateEngine
    import ai_quant_os.strategy_selector_sources as selector_sources_module

    started = threading.Event()

    def slow_observation(store, *, now):
        started.set()
        time.sleep(0.2)
        return selector_sources_module.ShadowPerformanceObservation(
            as_of=now, availability="NO_OBSERVATIONS", rows=()
        )

    monkeypatch.setattr(selector_sources_module, "shadow_performance_observation", slow_observation)
    sources = selector_sources_v3(
        (),
        WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW),
        now=NOW,
        registry_available=True,
        shadow_store=ShadowStore(tmp_path / "shadow.sqlite3"),
    )
    compiler = ContextCompiler(
        load_agent_catalog(),
        sources,
        clock=lambda: NOW,
        timeout_seconds=0.05,
        overall_timeout_seconds=0.5,
    )

    async def run():
        loop = asyncio.get_running_loop()
        began = loop.time()
        ticks = 0

        async def keep_loop_responsive():
            nonlocal ticks
            deadline = loop.time() + 0.08
            while loop.time() < deadline:
                ticks += 1
                await asyncio.sleep(0)

        context, _ = await asyncio.gather(
            compiler.compile("A12", "0.3.0", task_id="slow-shadow-scan", scope="PAPER"),
            keep_loop_responsive(),
        )
        elapsed = loop.time() - began
        performance = next(row for row in context.inputs if row.input_name == "performance")
        assert started.is_set()
        assert performance.status == "TIMEOUT"
        assert ticks > 10
        assert elapsed < 0.15

    asyncio.run(run())


def test_selector_v3_registry_read_obeys_its_own_quarantine(tmp_path):
    from ai_quant_os.agent_registry import ToolInvocation
    from ai_quant_os.shadow_store import ShadowStore
    from ai_quant_os.strategy_selector_service import StrategyRegistryRead

    registry, context = context_for(
        agent_version="0.3.0", shadow_store=ShadowStore(tmp_path / "shadow.sqlite3")
    )
    manifest = registry.get("A12", "0.3.0")
    registry.quarantine("A12", "0.3.0", manifest.manifest_ref)
    invocation = ToolInvocation(
        "A12",
        "0.3.0",
        "STRATEGY_REGISTRY_READ",
        "PAPER",
        {
            "registry_sha256": "0" * 64,
            "performance_sha256": "0" * 64,
        },
        context.context_ref,
        context.task_id,
    )
    service = StrategyRegistryRead(
        lambda: None,
        registry,
        context=lambda _: context,
        clock=lambda: NOW + timedelta(microseconds=1),
    )
    with pytest.raises(PermissionError, match="denied"):
        asyncio.run(service(invocation))

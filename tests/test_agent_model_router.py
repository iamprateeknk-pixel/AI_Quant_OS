"""Agent model router: governor binding, budgets, fallbacks, providers off."""

import asyncio

import pytest

from ai_quant_os.agent_model_router import AgentModelRouter, LlmResult
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.cost_governor import BudgetHierarchy, ModelTier, TaskRequest, TokenStormDetector

REGISTRY = load_agent_catalog()


async def deterministic():
    return "deterministic-output"


def small_budget(**overrides):
    values = dict(
        daily_platform_budget_tokens=10_000,
        squad_budget_tokens=5_000,
        agent_budget_tokens=1_000,
        task_token_cap=250_000,
        emergency_reserve_tokens=100,
    )
    values.update(overrides)
    return BudgetHierarchy(**values)


def test_every_manifest_routes_to_deterministic_with_providers_off():
    router = AgentModelRouter()
    assert router.enabled_providers == ()
    for manifest in REGISTRY.manifests():
        decision = router.route(REGISTRY, manifest.agent_id, manifest.agent_version, task_id="t")
        assert decision.executed_via == "DETERMINISTIC"
        assert decision.provider_id is None
        if manifest.squad in ("Market", "Research", "Learning", "Evolution"):
            assert decision.routed_tier is ModelTier.ROUTINE
            assert decision.fallback_reasons == ("PROVIDER_UNAVAILABLE",)
        elif manifest.squad == "Safety":
            assert decision.criticality == "incident"
            assert decision.routed_tier is ModelTier.DEEP
        else:
            assert decision.routed_tier in (ModelTier.ROUTINE, ModelTier.DEEP)


def test_deterministic_kind_bypasses_budgets_and_providers():
    router = AgentModelRouter(budget=small_budget())
    decision = router.route(REGISTRY, "A08", "0.2.0", task_id="t", kind="deterministic")
    assert decision.routed_tier is ModelTier.DETERMINISTIC
    assert decision.governor_decision == "ADMIT"
    assert decision.fallback_reasons == ()
    result = asyncio.run(
        router.execute(
            REGISTRY,
            "A08",
            "0.2.0",
            task_id="t",
            deterministic=deterministic,
            kind="deterministic",
        )
    )
    assert result[1] == "deterministic-output"


def test_low_priority_task_defers_when_agent_budget_exhausted():
    budget = small_budget()
    budget.record_usage(
        TaskRequest(task_id="seed", agent_id="A02", squad="Market", criticality="low"),
        1_000,
        0,
    )
    router = AgentModelRouter(budget=budget)
    decision = router.route(
        REGISTRY,
        "A02",
        "0.2.0",
        task_id="t",
        estimated_input_tokens=10,
        estimated_output_tokens=0,
    )
    assert decision.governor_decision == "DEFER"
    assert decision.fallback_reasons == ("BUDGET_DEFER",)
    assert decision.executed_via == "DETERMINISTIC"


def test_material_task_at_routine_is_denied_when_budget_exhausted():
    budget = small_budget()
    budget.record_usage(
        TaskRequest(task_id="seed", agent_id="A12", squad="Portfolio", criticality="material"),
        1_000,
        0,
    )
    router = AgentModelRouter(budget=budget)
    decision = router.route(
        REGISTRY,
        "A12",
        "0.2.0",
        task_id="t",
        estimated_input_tokens=10,
        estimated_output_tokens=0,
    )
    assert decision.governor_decision == "DENY"
    assert decision.fallback_reasons == ("BUDGET_DENY",)


def test_per_task_cap_downgrades_and_safety_is_never_denied():
    router = AgentModelRouter(budget=small_budget())
    decision = router.route(
        REGISTRY,
        "A18",
        "0.2.0",
        task_id="t",
        estimated_input_tokens=300_000,
        estimated_output_tokens=0,
    )
    # Incident work over the cap: downgraded FRONTIER path -> DEEP -> ROUTINE,
    # admitted at ROUTINE by the governor's safety guarantee; with no provider
    # registered it still degrades honestly to the deterministic path.
    assert decision.governor_decision in ("ADMIT", "DOWNGRADE")
    assert decision.routed_tier is ModelTier.ROUTINE
    assert decision.executed_via == "DETERMINISTIC"
    assert decision.fallback_reasons == ("PROVIDER_UNAVAILABLE",)


def test_provider_success_records_actual_usage():
    budget = small_budget()
    router = AgentModelRouter(budget=budget)

    async def invoke(task, tier):
        return LlmResult(output="llm-output", input_tokens=10, output_tokens=5)

    router.register_provider("test-provider", tiers={ModelTier.ROUTINE}, invoke=invoke)
    decision, output = asyncio.run(
        router.execute(REGISTRY, "A04", "0.2.0", task_id="t", deterministic=deterministic)
    )
    assert output == "llm-output"
    assert decision.executed_via == "PROVIDER"
    assert decision.provider_id == "test-provider"
    assert budget.agent_spent_tokens("A04") == 15


def test_provider_failure_degrades_with_reason_and_no_usage():
    budget = small_budget()
    router = AgentModelRouter(budget=budget)

    async def broken(task, tier):
        raise RuntimeError("provider down")

    router.register_provider("broken", tiers={ModelTier.ROUTINE}, invoke=broken)
    decision, output = asyncio.run(
        router.execute(REGISTRY, "A04", "0.2.0", task_id="t", deterministic=deterministic)
    )
    assert output == "deterministic-output"
    assert decision.executed_via == "DETERMINISTIC"
    assert decision.fallback_reasons == ("PROVIDER_FAILED",)
    assert budget.agent_spent_tokens("A04") == 0


def test_token_storm_forces_deterministic_fallback():
    router = AgentModelRouter(
        storm_detector=TokenStormDetector(
            window_seconds=60.0, max_calls_per_window=30, loop_repeat_threshold=3
        ),
        clock=lambda: 1_000.0,
    )

    async def invoke(task, tier):
        return LlmResult(output="llm-output", input_tokens=1, output_tokens=1)

    router.register_provider("p", tiers={ModelTier.ROUTINE}, invoke=invoke)
    outputs = []
    decisions = []
    for _ in range(3):
        decision, output = asyncio.run(
            router.execute(
                REGISTRY,
                "A04",
                "0.2.0",
                task_id="looping-task",
                deterministic=deterministic,
            )
        )
        decisions.append(decision)
        outputs.append(output)
    assert outputs[:2] == ["llm-output", "llm-output"]
    assert outputs[2] == "deterministic-output"
    assert decisions[2].fallback_reasons == ("TOKEN_STORM",)


def test_provider_registration_is_explicit_and_validated():
    router = AgentModelRouter()

    async def invoke(task, tier):
        return LlmResult(output=None, input_tokens=0, output_tokens=0)

    with pytest.raises(ValueError):
        router.register_provider("bad", tiers={ModelTier.DETERMINISTIC}, invoke=invoke)
    router.register_provider("ok", tiers={ModelTier.DEEP}, invoke=invoke)
    assert router.enabled_providers == ("ok",)
    with pytest.raises(ValueError):
        router.register_provider("ok", tiers={ModelTier.DEEP}, invoke=invoke)


def test_escalation_flags_raise_tier_but_not_for_low_work():
    router = AgentModelRouter()
    material = router.route(REGISTRY, "A10", "0.2.0", task_id="t", uncertainty=True)
    assert material.routed_tier is ModelTier.DEEP
    low = router.route(REGISTRY, "A08", "0.2.0", task_id="t", uncertainty=True)
    assert low.routed_tier is ModelTier.ROUTINE
    critical = router.route(REGISTRY, "A18", "0.2.0", task_id="t", verifier_failed=True)
    assert critical.routed_tier is ModelTier.FRONTIER
    assert critical.fallback_reasons == ("PROVIDER_UNAVAILABLE",)

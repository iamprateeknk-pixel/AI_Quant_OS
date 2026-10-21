from decimal import Decimal

import pytest

from ai_quant_os.cost_governor import (
    BudgetHierarchy,
    CostValueScore,
    ModelTier,
    TaskRequest,
    TokenStormDetector,
    cache_key,
    route_model,
    score_agent,
)


def task(
    criticality="material",
    *,
    task_id="task-1",
    agent_id="A08",
    squad="research",
    input_tokens=1_000,
    output_tokens=500,
    kind="llm",
):
    return TaskRequest(
        task_id=task_id,
        agent_id=agent_id,
        squad=squad,
        criticality=criticality,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        kind=kind,
    )


def small_budget(**overrides):
    params = {
        "daily_platform_budget_tokens": 10_000,
        "squad_budget_tokens": 6_000,
        "agent_budget_tokens": 4_000,
        "task_token_cap": 2_000,
        "emergency_reserve_tokens": 2_000,
    }
    params.update(overrides)
    return BudgetHierarchy(**params)


# --- routing ---------------------------------------------------------------


def test_deterministic_kind_never_routes_to_an_llm():
    assert route_model(task("critical", kind="deterministic")) is ModelTier.DETERMINISTIC
    assert route_model(task("incident", kind="deterministic"), uncertainty=True) is (
        ModelTier.DETERMINISTIC
    )


def test_low_criticality_is_always_routine_even_with_escalation_flags():
    assert route_model(task("low")) is ModelTier.ROUTINE
    assert route_model(task("low"), uncertainty=True, disagreement=True) is ModelTier.ROUTINE


def test_material_is_routine_and_escalates_to_deep():
    assert route_model(task("material")) is ModelTier.ROUTINE
    assert route_model(task("material"), uncertainty=True) is ModelTier.DEEP
    assert route_model(task("material"), disagreement=True) is ModelTier.DEEP
    assert route_model(task("material"), verifier_failed=True) is ModelTier.DEEP


def test_critical_and_incident_are_deep_and_escalate_to_frontier():
    assert route_model(task("critical")) is ModelTier.DEEP
    assert route_model(task("critical"), verifier_failed=True) is ModelTier.FRONTIER
    assert route_model(task("incident")) is ModelTier.DEEP
    assert route_model(task("incident"), disagreement=True) is ModelTier.FRONTIER


def test_task_request_validation():
    with pytest.raises(ValueError):
        task("urgent")
    with pytest.raises(ValueError):
        task("low", kind="shell")
    with pytest.raises(ValueError):
        task("low", input_tokens=-1)


# --- budget hierarchy --------------------------------------------------------


def test_admit_within_all_budgets():
    budget = small_budget()
    admission = budget.admit(task("material"), ModelTier.ROUTINE)
    assert admission.decision == "ADMIT"
    assert admission.tier is ModelTier.ROUTINE


def test_deterministic_tier_is_always_admitted():
    budget = small_budget()
    admission = budget.admit(task("low", kind="deterministic"), ModelTier.DETERMINISTIC)
    assert admission.decision == "ADMIT"


def test_task_over_per_task_cap_is_downgraded_one_tier():
    budget = small_budget()
    big = task("critical", input_tokens=1_800, output_tokens=500)  # 2_300 > 2_000 cap
    admission = budget.admit(big, ModelTier.FRONTIER)
    assert admission.decision == "DOWNGRADE"
    assert admission.tier is ModelTier.DEEP
    admission = budget.admit(big, ModelTier.DEEP)
    assert admission.decision == "DOWNGRADE"
    assert admission.tier is ModelTier.ROUTINE


def test_task_over_per_task_cap_is_denied_at_routine():
    budget = small_budget()
    big = task("material", input_tokens=1_800, output_tokens=500)
    admission = budget.admit(big, ModelTier.ROUTINE)
    assert admission.decision == "DENY"


def test_platform_exhaustion_defers_low_and_downgrades_or_denies_others():
    budget = small_budget()
    # Non-reserve platform headroom is 10_000 - 2_000 = 8_000.
    budget.record_usage(task("material", agent_id="A01", squad="ops"), 7_500, 400)
    assert budget.platform_spent_tokens == 7_900

    low = budget.admit(task("low", input_tokens=200, output_tokens=0), ModelTier.ROUTINE)
    assert low.decision == "DEFER"

    deep_task = task("material", input_tokens=200, output_tokens=0)
    material_deep = budget.admit(deep_task, ModelTier.DEEP)
    assert material_deep.decision == "DOWNGRADE"
    assert material_deep.tier is ModelTier.ROUTINE

    material_routine = budget.admit(
        task("material", input_tokens=200, output_tokens=0), ModelTier.ROUTINE
    )
    assert material_routine.decision == "DENY"


def test_squad_and_agent_budgets_are_tracked_and_enforced():
    budget = small_budget()
    worker = task("material", agent_id="A08", squad="research")
    budget.record_usage(worker, 3_900, 100)
    assert budget.agent_spent_tokens("A08") == 4_000
    assert budget.squad_spent_tokens("research") == 4_000

    denied = budget.admit(task("material", input_tokens=100, output_tokens=0), ModelTier.ROUTINE)
    assert denied.decision == "DENY"
    assert "agent" in denied.reason

    other_agent = budget.admit(
        task("material", agent_id="A09", input_tokens=100, output_tokens=0), ModelTier.ROUTINE
    )
    assert other_agent.decision == "ADMIT"

    budget.record_usage(task("material", agent_id="A09", squad="research"), 1_900, 100)
    assert budget.squad_spent_tokens("research") == 6_000
    deferred = budget.admit(
        task("low", agent_id="A10", input_tokens=100, output_tokens=0), ModelTier.ROUTINE
    )
    assert deferred.decision == "DEFER"
    assert "squad" in deferred.reason

    other_squad = budget.admit(
        task("material", agent_id="A10", squad="validation", input_tokens=100, output_tokens=0),
        ModelTier.ROUTINE,
    )
    assert other_squad.decision == "ADMIT"


def test_incident_tasks_are_never_denied_or_deferred_for_budget():
    budget = small_budget()
    # Spend far beyond the whole platform budget, reserve included.
    budget.record_usage(task("material"), 20_000, 20_000)
    incident = task("incident", input_tokens=100, output_tokens=50)
    admission = budget.admit(incident, ModelTier.DEEP)
    assert admission.decision == "ADMIT"
    assert admission.tier is ModelTier.DEEP


def test_incident_task_over_per_task_cap_is_downgraded_never_denied():
    budget = small_budget()
    big_incident = task("incident", input_tokens=5_000, output_tokens=0)
    admission = budget.admit(big_incident, ModelTier.FRONTIER)
    assert admission.decision == "DOWNGRADE"
    assert admission.tier is ModelTier.DEEP
    admission = budget.admit(big_incident, ModelTier.ROUTINE)
    assert admission.decision == "ADMIT"


def test_record_usage_rejects_negative_tokens():
    budget = small_budget()
    with pytest.raises(ValueError):
        budget.record_usage(task("low"), -1, 0)


# --- token storm detector ----------------------------------------------------


def test_storm_when_calls_in_window_exceed_threshold():
    detector = TokenStormDetector(window_seconds=60.0, max_calls_per_window=5)
    for index in range(5):
        detector.observe(f"task-{index}", "A08", 100.0 + index)
    assert detector.is_storm("A08", 105.0) is False
    detector.observe("task-5", "A08", 106.0)
    assert detector.is_storm("A08", 106.0) is True


def test_calls_outside_the_window_do_not_count():
    detector = TokenStormDetector(window_seconds=60.0, max_calls_per_window=5)
    for index in range(6):
        detector.observe(f"task-{index}", "A08", 100.0 + index)
    assert detector.is_storm("A08", 1_000.0) is False
    assert detector.is_storm("A09", 106.0) is False


def test_repeated_same_task_id_is_a_handoff_loop_storm():
    detector = TokenStormDetector(
        window_seconds=60.0, max_calls_per_window=100, loop_repeat_threshold=3
    )
    detector.observe("same-task", "A16", 10.0)
    detector.observe("same-task", "A16", 11.0)
    assert detector.is_storm("A16", 11.0) is False
    detector.observe("same-task", "A16", 12.0)
    assert detector.is_storm("A16", 12.0) is True


# --- cost/value scorecard ----------------------------------------------------


def test_score_agent_formula():
    # quality 0.9 * acceptance 8/10 - cost $20 / 10 tasks = 0.72 - 2 = -1.28
    value = score_agent(
        tasks_completed=10,
        tasks_accepted=8,
        total_cost_usd=Decimal("20"),
        quality=Decimal("0.9"),
    )
    assert value == Decimal("-1.28")


def test_score_agent_with_no_completed_tasks_is_zero():
    value = score_agent(
        tasks_completed=0,
        tasks_accepted=0,
        total_cost_usd=Decimal("0"),
        quality=Decimal("0.5"),
    )
    assert value == Decimal("0")


def test_score_agent_validation_and_scorecard_row():
    with pytest.raises(ValueError):
        score_agent(
            tasks_completed=2,
            tasks_accepted=3,
            total_cost_usd=Decimal("0"),
            quality=Decimal("0.5"),
        )
    with pytest.raises(ValueError):
        score_agent(
            tasks_completed=2,
            tasks_accepted=1,
            total_cost_usd=Decimal("0"),
            quality=Decimal("1.5"),
        )
    row = CostValueScore.compute(
        "A08",
        tasks_completed=10,
        tasks_accepted=8,
        total_cost_usd=Decimal("20"),
        quality=Decimal("0.9"),
    )
    assert row.agent_id == "A08"
    assert row.value_score == Decimal("-1.28")


# --- prompt cache key ---------------------------------------------------------


def test_cache_key_is_stable_and_component_sensitive():
    key = cache_key("system prompt", "evidence-1", "tools-v1")
    assert key == cache_key("system prompt", "evidence-1", "tools-v1")
    assert len(key) == 64
    assert key != cache_key("other prompt", "evidence-1", "tools-v1")
    assert key != cache_key("system prompt", "evidence-2", "tools-v1")
    assert key != cache_key("system prompt", "evidence-1", "tools-v2")

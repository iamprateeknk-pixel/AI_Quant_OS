from dataclasses import replace

import pytest

from ai_quant_os.incident_policy import IncidentFacts, assess_incident
from ai_quant_os.risk import SafetyState


def healthy_facts():
    return IncidentFacts(
        SafetyState.NORMAL,
        SafetyState.NORMAL,
        True,
        False,
        (),
        0,
        True,
        0,
        ("FRESH",) * 4,
        ("FRESH",) * 4,
        True,
        0,
    )


@pytest.mark.parametrize("state", list(SafetyState))
def test_healthy_observations_preserve_existing_safety_floor(state):
    assessment = assess_incident(replace(healthy_facts(), live_state=state, durable_state=state))
    assert assessment.recommended_state == state
    assert assessment.findings == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"future_incidents": -1},
        {"future_fills": True},
        {"uncertain_orders": -1},
        {"quote_statuses": ()},
        {"history_statuses": ("FRESH",)},
        {"watchdog_available": "yes"},
        {"live_state": "NORMAL"},
    ],
)
def test_invalid_policy_facts_are_refused(changes):
    with pytest.raises(ValueError):
        assess_incident(replace(healthy_facts(), **changes))

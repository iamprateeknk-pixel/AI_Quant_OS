"""V3.2 section 7: agent councils and the material decision protocol."""

import pytest

from ai_quant_os.councils import COUNCILS, CouncilDecision, run_council_decision


def _decide(council: str = "research", **overrides):
    kwargs = {
        "proposer": "hypothesis",
        "verifier_passed": True,
        "policy_passed": True,
    }
    kwargs.update(overrides)
    return run_council_decision(council, "proposal-1", **kwargs)


def test_council_membership_matches_blueprint():
    assert COUNCILS == {
        "market_state": ("regime", "macro", "rates", "volatility", "liquidity", "event"),
        "research": ("hypothesis", "feature", "strategy_designer", "statistician", "skeptic"),
        "validation": (
            "oos",
            "walk_forward",
            "monte_carlo",
            "leakage",
            "multiple_testing",
            "capacity",
            "red_team",
        ),
        "portfolio": ("tournament", "correlation", "allocation", "drawdown", "capacity"),
        "execution": ("execution_planner", "liquidity", "tca", "broker_health"),
        "incident": (
            "broker_health",
            "data_quality",
            "reconciliation",
            "security",
            "incident_commander",
        ),
    }


def test_clean_proposal_is_accepted_with_full_record():
    decision = _decide()
    assert isinstance(decision, CouncilDecision)
    assert decision.verdict == "ACCEPTED"
    assert decision.council == "research"
    assert decision.proposal_id == "proposal-1"
    assert decision.proposer == "hypothesis"
    assert decision.challenger_objections == ()
    assert decision.verifier_passed is True
    assert decision.policy_passed is True
    assert decision.hard_fail is False
    assert decision.rationale


@pytest.mark.parametrize("council", sorted(COUNCILS))
def test_every_council_can_accept(council: str):
    decision = _decide(council, proposer=COUNCILS[council][0])
    assert decision.verdict == "ACCEPTED"
    assert decision.council == council


def test_hard_fail_rejects_even_when_everything_else_passes():
    decision = _decide("validation", proposer="oos", hard_fail=True)
    assert decision.verdict == "REJECTED"
    assert decision.hard_fail is True


def test_hard_fail_wins_over_escalation_paths():
    decision = _decide(
        hard_fail=True,
        challenger_objections=("unresolved",),
        requires_human=True,
    )
    assert decision.verdict == "REJECTED"


def test_verifier_failure_rejects():
    decision = _decide(verifier_passed=False)
    assert decision.verdict == "REJECTED"
    assert "Verifier" in decision.rationale


def test_policy_failure_rejects():
    decision = _decide(policy_passed=False)
    assert decision.verdict == "REJECTED"
    assert "Policy" in decision.rationale


def test_unresolved_challenger_objections_escalate_not_reject():
    decision = _decide(challenger_objections=("skeptic: sample too small",))
    assert decision.verdict == "ESCALATED"
    assert decision.challenger_objections == ("skeptic: sample too small",)


def test_human_approval_resolves_objection_escalation():
    decision = _decide(
        challenger_objections=("skeptic: sample too small",),
        human_approval=True,
    )
    assert decision.verdict == "ACCEPTED"


def test_requires_human_escalates_until_approved():
    assert _decide(requires_human=True).verdict == "ESCALATED"
    assert _decide(requires_human=True, human_approval=False).verdict == "ESCALATED"
    assert _decide(requires_human=True, human_approval=None).verdict == "ESCALATED"
    assert _decide(requires_human=True, human_approval=True).verdict == "ACCEPTED"


def test_objections_and_human_requirement_together_still_escalate():
    decision = _decide(
        challenger_objections=("correlation: hidden common factor",),
        requires_human=True,
    )
    assert decision.verdict == "ESCALATED"


def test_rejection_takes_priority_over_human_escalation():
    decision = _decide(verifier_passed=False, requires_human=True)
    assert decision.verdict == "REJECTED"


def test_unknown_council_raises_value_error():
    with pytest.raises(ValueError):
        run_council_decision(
            "not_a_council",
            "proposal-1",
            proposer="hypothesis",
            verifier_passed=True,
            policy_passed=True,
        )
    with pytest.raises(ValueError):
        CouncilDecision(
            council="not_a_council",
            proposal_id="proposal-1",
            verdict="ACCEPTED",
            proposer="hypothesis",
        )


def test_decision_rejects_an_invalid_verdict():
    with pytest.raises(ValueError):
        CouncilDecision(
            council="research",
            proposal_id="proposal-1",
            verdict="MAYBE",
            proposer="hypothesis",
        )

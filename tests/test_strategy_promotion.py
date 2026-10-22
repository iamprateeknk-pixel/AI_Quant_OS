"""Strategy promotion gate: fortress leg plus strategy council leg.

Both legs are mandatory and independently block promotion. The council
inputs here are all-green (verifier and policy pass, no objections, no
hard fail) unless a test deliberately breaks one of them, so any block
in those tests comes from the leg the test is exercising.
"""

import pytest

from ai_quant_os.councils import COUNCILS
from ai_quant_os.strategy_promotion import (
    STRATEGY_COUNCIL,
    PromotionRequest,
    evaluate_promotion,
)
from ai_quant_os.validation_fortress import FortressVerdict


def _verdict(passed: bool) -> FortressVerdict:
    return FortressVerdict(passed=passed, gates=(), metrics={})


def _request(**overrides) -> PromotionRequest:
    kwargs = {
        "strategy_id": "strategy-1",
        "hypothesis_id": "hypothesis-1",
        "fortress_verdict": _verdict(True),
        "fortress_hypothesis_id": "hypothesis-1",
        "verifier_passed": True,
        "policy_passed": True,
    }
    kwargs.update(overrides)
    return PromotionRequest(**kwargs)


def _decide(**overrides):
    return evaluate_promotion(_request(**overrides))


def test_passing_fortress_and_accepted_council_promotes():
    decision = _decide()
    assert decision.promoted is True
    assert decision.fortress_passed is True
    assert decision.council_verdict == "ACCEPTED"
    assert decision.council_decision is not None
    assert decision.council_decision.council == STRATEGY_COUNCIL
    assert decision.strategy_id == "strategy-1"
    assert decision.hypothesis_id == "hypothesis-1"
    assert decision.reasons == ()


def test_failed_fortress_blocks_even_when_council_accepts():
    decision = _decide(fortress_verdict=_verdict(False))
    assert decision.promoted is False
    assert decision.fortress_passed is False
    assert decision.council_verdict == "ACCEPTED"
    assert any("fortress verdict is FAIL" in reason for reason in decision.reasons)


def test_verifier_failure_rejects_in_council_and_blocks():
    decision = _decide(verifier_passed=False)
    assert decision.promoted is False
    assert decision.fortress_passed is True
    assert decision.council_verdict == "REJECTED"
    assert any("strategy council verdict REJECTED" in reason for reason in decision.reasons)


def test_unresolved_objections_escalate_and_block():
    decision = _decide(challenger_objections=("skeptic: sample too small",))
    assert decision.promoted is False
    assert decision.fortress_passed is True
    assert decision.council_verdict == "ESCALATED"
    assert any("strategy council verdict ESCALATED" in reason for reason in decision.reasons)


def test_objections_resolved_by_human_approval_promote():
    decision = _decide(
        challenger_objections=("skeptic: sample too small",),
        human_approval=True,
    )
    assert decision.promoted is True
    assert decision.council_verdict == "ACCEPTED"
    assert decision.reasons == ()


def test_missing_fortress_verdict_blocks():
    decision = _decide(fortress_verdict=None, fortress_hypothesis_id=None)
    assert decision.promoted is False
    assert decision.fortress_passed is False
    assert "no fortress verdict" in decision.reasons


def test_fortress_verdict_for_different_hypothesis_blocks():
    decision = _decide(fortress_hypothesis_id="hypothesis-2")
    assert decision.promoted is False
    assert decision.fortress_passed is False
    assert "fortress verdict was issued for a different hypothesis" in decision.reasons


def test_hard_fail_blocks():
    decision = _decide(hard_fail=True)
    assert decision.promoted is False
    assert decision.council_verdict == "REJECTED"
    assert decision.council_decision is not None
    assert decision.council_decision.hard_fail is True


def test_empty_strategy_id_raises_value_error():
    with pytest.raises(ValueError):
        _request(strategy_id="")


def test_empty_hypothesis_id_raises_value_error():
    with pytest.raises(ValueError):
        _request(hypothesis_id="")


def test_strategy_council_is_the_research_council():
    assert STRATEGY_COUNCIL == "research"
    assert STRATEGY_COUNCIL in COUNCILS
    assert "strategy_designer" in COUNCILS[STRATEGY_COUNCIL]

"""Promotion lifecycle consumer: gate outcomes drive registry status.

The consumer must never weaken the gate: a strategy becomes
PAPER_ACTIVE only when evaluate_promotion promoted in the same call,
and the recorded PromotionDecision must carry the gate evidence that
the contracts require for approval.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ai_quant_os.promotion_lifecycle import (
    NOT_PROMOTED,
    PAPER_ACTIVE,
    PromotionAttempt,
    PromotionRegistry,
    fortress_verdict_ref,
    request_promotion,
)
from ai_quant_os.promotion_store import PromotionEvidenceStore
from ai_quant_os.strategy_promotion import STRATEGY_COUNCIL, PromotionRequest
from ai_quant_os.validation_fortress import FortressVerdict, GateResult

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)


def _gate_result(name: str, passed: bool) -> GateResult:
    return GateResult(
        gate=name,
        passed=passed,
        metric=1.0 if passed else -1.0,
        threshold="test threshold",
        reason="gate passed" if passed else "gate failed",
    )


def _passing_verdict() -> FortressVerdict:
    return FortressVerdict(
        passed=True,
        gates=(
            _gate_result("DATA_INTEGRITY", True),
            _gate_result("EXPECTANCY", True),
        ),
        metrics={"total_net_return": 0.5, "annualized_sharpe": 1.25},
    )


def _failing_verdict() -> FortressVerdict:
    return FortressVerdict(
        passed=False,
        gates=(
            _gate_result("DATA_INTEGRITY", True),
            _gate_result("EXPECTANCY", False),
        ),
        metrics={"total_net_return": -0.5, "annualized_sharpe": -1.25},
    )


def _request(**overrides) -> PromotionRequest:
    kwargs = {
        "strategy_id": "strategy-1",
        "hypothesis_id": "hypothesis-1",
        "fortress_verdict": _passing_verdict(),
        "fortress_hypothesis_id": "hypothesis-1",
        "verifier_passed": True,
        "policy_passed": True,
    }
    kwargs.update(overrides)
    return PromotionRequest(**kwargs)


@pytest.fixture()
def stores(tmp_path):
    registry = PromotionRegistry(tmp_path / "promotion-registry.sqlite3")
    evidence_store = PromotionEvidenceStore(tmp_path / "promotion-evidence.sqlite3")
    return registry, evidence_store


def test_failed_fortress_refuses_and_records_attempt(stores):
    registry, evidence_store = stores
    outcome = request_promotion(
        registry, evidence_store, _request(fortress_verdict=_failing_verdict()), now=NOW
    )

    assert outcome.promoted is False
    assert outcome.status == NOT_PROMOTED
    assert registry.status_for("strategy-1") == NOT_PROMOTED
    attempts = registry.attempts_for("strategy-1")
    assert len(attempts) == 1
    assert attempts[0].gate_promoted is False
    assert attempts[0].status_after == NOT_PROMOTED
    decision = evidence_store.get(outcome.decision_ref)
    assert decision.approved_by_policy is False
    assert decision.decision == "REJECTED"
    assert decision.offline_eval.status == "FAIL"
    assert decision.offline_eval.evidence_refs == (fortress_verdict_ref(_failing_verdict()),)


def test_missing_fortress_verdict_refuses_and_records_attempt(stores):
    registry, evidence_store = stores
    outcome = request_promotion(
        registry,
        evidence_store,
        _request(fortress_verdict=None, fortress_hypothesis_id=None),
        now=NOW,
    )

    assert outcome.promoted is False
    assert registry.status_for("strategy-1") == NOT_PROMOTED
    assert len(registry.attempts_for("strategy-1")) == 1
    decision = evidence_store.get(outcome.decision_ref)
    assert decision.approved_by_policy is False
    assert decision.offline_eval.status == "UNAVAILABLE"
    assert decision.fortress_verdict_ref is None


def test_rejected_council_refuses_despite_passing_fortress(stores):
    registry, evidence_store = stores
    outcome = request_promotion(registry, evidence_store, _request(verifier_passed=False), now=NOW)

    assert outcome.promoted is False
    assert outcome.gate_decision.fortress_passed is True
    assert outcome.gate_decision.council_verdict == "REJECTED"
    assert registry.status_for("strategy-1") == NOT_PROMOTED
    decision = evidence_store.get(outcome.decision_ref)
    assert decision.approved_by_policy is False
    assert decision.council_verdict == "REJECTED"
    assert decision.offline_eval.status == "PASS"


def test_escalated_council_refuses_despite_passing_fortress(stores):
    registry, evidence_store = stores
    outcome = request_promotion(
        registry,
        evidence_store,
        _request(challenger_objections=("skeptic: sample too small",)),
        now=NOW,
    )

    assert outcome.promoted is False
    assert outcome.gate_decision.council_verdict == "ESCALATED"
    assert registry.status_for("strategy-1") == NOT_PROMOTED
    assert evidence_store.get(outcome.decision_ref).approved_by_policy is False


def test_fortress_verdict_for_wrong_hypothesis_refuses(stores):
    registry, evidence_store = stores
    outcome = request_promotion(
        registry, evidence_store, _request(fortress_hypothesis_id="hypothesis-2"), now=NOW
    )

    assert outcome.promoted is False
    assert outcome.gate_decision.fortress_passed is False
    assert registry.status_for("strategy-1") == NOT_PROMOTED
    decision = evidence_store.get(outcome.decision_ref)
    assert decision.approved_by_policy is False
    assert decision.offline_eval.status == "FAIL"
    assert decision.fortress_hypothesis_id == "hypothesis-2"


def test_both_legs_green_promotes_to_paper_active(stores):
    registry, evidence_store = stores
    verdict = _passing_verdict()
    outcome = request_promotion(
        registry, evidence_store, _request(fortress_verdict=verdict), now=NOW
    )

    assert outcome.promoted is True
    assert outcome.status == PAPER_ACTIVE
    assert outcome.reasons == ()
    assert registry.status_for("strategy-1") == PAPER_ACTIVE

    decision = evidence_store.get(outcome.decision_ref)
    assert decision.candidate_id == "strategy-1"
    assert decision.approved_by_policy is True
    assert decision.decision == "PROMOTED"
    assert decision.fortress_hypothesis_id == "hypothesis-1"
    assert decision.fortress_verdict_ref == fortress_verdict_ref(verdict)
    assert decision.council_name == STRATEGY_COUNCIL
    assert decision.council_verdict == "ACCEPTED"
    assert decision.decided_at == NOW
    assert decision.offline_eval.status == "PASS"
    assert decision.offline_eval.evidence_refs == (fortress_verdict_ref(verdict),)
    for evaluation in (
        decision.replay_eval,
        decision.adversarial_eval,
        decision.shadow_eval,
        decision.canary_eval,
    ):
        assert evaluation.status == "NOT_RUN"
        assert evaluation.reason == "not part of the fortress+council promotion gate"

    attempts = registry.attempts_for("strategy-1")
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.candidate_id == "strategy-1"
    assert attempt.hypothesis_id == "hypothesis-1"
    assert attempt.fortress_verdict_ref == decision.fortress_verdict_ref
    assert attempt.council_name == STRATEGY_COUNCIL
    assert attempt.council_verdict == "ACCEPTED"
    assert attempt.council_proposal_id == "strategy-1"
    assert attempt.evidence_ref == outcome.decision_ref
    assert attempt.status_after == PAPER_ACTIVE

    reopened = PromotionRegistry(registry.path)
    assert reopened.status_for("strategy-1") == PAPER_ACTIVE


def test_repeat_request_is_idempotent(stores):
    registry, evidence_store = stores
    first = request_promotion(registry, evidence_store, _request(), now=NOW)
    second = request_promotion(registry, evidence_store, _request(), now=LATER)

    assert first.promoted is True
    assert second.promoted is True
    assert second.decision_ref == first.decision_ref
    assert second.status == PAPER_ACTIVE
    assert len(registry.attempts_for("strategy-1")) == 1
    assert evidence_store.list_refs() == (first.decision_ref,)


def test_repeat_refusal_is_idempotent(stores):
    registry, evidence_store = stores
    request = _request(fortress_verdict=_failing_verdict())
    first = request_promotion(registry, evidence_store, request, now=NOW)
    second = request_promotion(registry, evidence_store, request, now=LATER)

    assert first.promoted is False
    assert second.promoted is False
    assert second.decision_ref == first.decision_ref
    assert len(registry.attempts_for("strategy-1")) == 1


def test_new_evidence_after_refusal_records_a_new_attempt(stores):
    registry, evidence_store = stores
    refused = request_promotion(
        registry, evidence_store, _request(fortress_verdict=_failing_verdict()), now=NOW
    )
    promoted = request_promotion(registry, evidence_store, _request(), now=LATER)

    assert refused.promoted is False
    assert promoted.promoted is True
    assert promoted.decision_ref != refused.decision_ref
    assert registry.status_for("strategy-1") == PAPER_ACTIVE
    assert len(registry.attempts_for("strategy-1")) == 2


def test_second_strategy_promotion_does_not_affect_the_first(stores):
    registry, evidence_store = stores
    refused = request_promotion(
        registry, evidence_store, _request(fortress_verdict=_failing_verdict()), now=NOW
    )
    promoted = request_promotion(
        registry, evidence_store, _request(strategy_id="strategy-2"), now=NOW
    )

    assert refused.status == NOT_PROMOTED
    assert promoted.status == PAPER_ACTIVE
    assert registry.status_for("strategy-1") == NOT_PROMOTED
    assert registry.status_for("strategy-2") == PAPER_ACTIVE
    assert len(registry.attempts_for("strategy-1")) == 1
    assert len(registry.attempts_for("strategy-2")) == 1


def test_paper_active_strategy_stays_paper_active_after_later_refusal(stores):
    registry, evidence_store = stores
    request_promotion(registry, evidence_store, _request(), now=NOW)
    outcome = request_promotion(
        registry, evidence_store, _request(fortress_verdict=_failing_verdict()), now=LATER
    )

    assert outcome.promoted is False
    assert outcome.status == PAPER_ACTIVE
    assert registry.status_for("strategy-1") == PAPER_ACTIVE
    assert len(registry.attempts_for("strategy-1")) == 2


def test_naive_timestamp_is_refused(stores):
    registry, evidence_store = stores
    with pytest.raises(ValueError):
        request_promotion(registry, evidence_store, _request(), now=datetime(2026, 1, 1))
    assert registry.attempts_for("strategy-1") == ()


# --- PromotionAttempt store round-trip ---------------------------------------


def _attempt(**overrides):
    kwargs = {
        "strategy_id": "strategy-1",
        "candidate_id": "strategy-1",
        "hypothesis_id": "hypothesis-1",
        "fortress_hypothesis_id": None,
        "fortress_verdict_ref": None,
        "fortress_passed": False,
        "council_name": "research",
        "council_verdict": "REJECTED",
        "council_proposal_id": "proposal-1",
        "gate_promoted": False,
        "gate_reasons": ("fortress failed",),
        "decision": "REJECTED",
        "approved_by_policy": False,
        "evidence_ref": "promotion-decision:" + "a" * 64,
        "status_after": NOT_PROMOTED,
        "attempted_at": "2026-01-01T00:00:00+00:00",
    }
    kwargs.update(overrides)
    return PromotionAttempt(**kwargs)


def test_attempt_to_dict_from_dict_round_trip():
    attempt = _attempt()
    record = attempt.to_dict()
    assert isinstance(record["gate_reasons"], list)
    assert record["gate_reasons"] == ["fortress failed"]
    assert PromotionAttempt.from_dict(record) == attempt
    assert PromotionAttempt.from_dict(attempt.to_dict()).to_dict() == record


def test_attempt_from_dict_rejects_shape_mismatch():
    record = _attempt().to_dict()
    del record["decision"]
    with pytest.raises(ValueError, match="shape mismatch"):
        PromotionAttempt.from_dict(record)
    with pytest.raises(ValueError, match="JSON object"):
        PromotionAttempt.from_dict([_attempt().to_dict()])


def test_registry_attempt_store_round_trip(stores):
    registry, _evidence_store = stores
    attempt = _attempt()
    ref = registry.record_attempt(attempt)
    assert ref.startswith("promotion-attempt:")
    assert registry.get_attempt(ref) == attempt
    # Recording the same attempt again is idempotent: same ref, one row.
    assert registry.record_attempt(attempt) == ref
    assert registry.list_refs() == (ref,)
    found = registry.find_attempt(
        strategy_id="strategy-1",
        hypothesis_id="hypothesis-1",
        fortress_hypothesis_id=None,
        fortress_verdict_ref=None,
        council_name="research",
        council_verdict="REJECTED",
        council_proposal_id="proposal-1",
    )
    assert found == attempt
    assert (
        registry.find_attempt(
            strategy_id="strategy-1",
            hypothesis_id="hypothesis-1",
            fortress_hypothesis_id=None,
            fortress_verdict_ref=None,
            council_name="research",
            council_verdict="REJECTED",
            council_proposal_id="proposal-2",
        )
        is None
    )
    with pytest.raises(ValueError, match="Unknown promotion attempt reference"):
        registry.get_attempt("promotion-attempt:" + "b" * 64)

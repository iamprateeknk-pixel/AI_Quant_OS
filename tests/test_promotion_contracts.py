from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ai_quant_os.promotion_contracts import PromotionDecision, promotion_decision_schema

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PENDING = {"status": "NOT_RUN", "reason": "GATE_NOT_CONNECTED"}
GATE_EVIDENCE = {
    "fortress_hypothesis_id": "hypothesis-1",
    "fortress_verdict_ref": "a" * 64,
    "council_name": "research",
    "council_verdict": "ACCEPTED",
    "decided_at": NOW,
}


def _base(**overrides):
    base = {
        "candidate_id": "candidate-1",
        "current_champion_id": None,
        "offline_eval": PENDING,
        "replay_eval": PENDING,
        "adversarial_eval": PENDING,
        "shadow_eval": PENDING,
        "canary_eval": PENDING,
        "decision": "WAIT_FOR_EVIDENCE",
        "rollback_target": None,
        "created_at": NOW,
    }
    base.update(overrides)
    return base


def test_promotion_decision_fails_closed_when_evaluations_are_unavailable():
    decision = PromotionDecision(
        candidate_id="candidate-1",
        current_champion_id="champion-1",
        offline_eval={"status": "UNAVAILABLE", "reason": "SOURCE_NOT_CONNECTED"},
        replay_eval=PENDING,
        adversarial_eval=PENDING,
        shadow_eval=PENDING,
        canary_eval=PENDING,
        decision="WAIT_FOR_EVIDENCE",
        rollback_target="champion-1",
        created_at=NOW,
    )
    assert decision.approved_by_policy is False
    assert decision.decision == "WAIT_FOR_EVIDENCE"


def test_promotion_decision_rejects_unearned_approval_and_unknown_fields():
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(_base(approved_by_policy=True))
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(_base(unexpected="accepted"))
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(_base(decision="PROMOTE"))


def test_promotion_approval_with_complete_gate_evidence_validates():
    decision = PromotionDecision.model_validate(
        _base(decision="PROMOTED", approved_by_policy=True, **GATE_EVIDENCE)
    )
    assert decision.approved_by_policy is True
    assert decision.decision == "PROMOTED"
    assert decision.fortress_hypothesis_id == "hypothesis-1"
    assert decision.fortress_verdict_ref == "a" * 64
    assert decision.council_name == "research"
    assert decision.council_verdict == "ACCEPTED"
    assert decision.decided_at == NOW


@pytest.mark.parametrize(
    "missing",
    [
        {"fortress_hypothesis_id": None},
        {"fortress_verdict_ref": None},
        {"council_name": None},
        {"council_verdict": None},
        {"council_verdict": "REJECTED"},
        {"council_verdict": "ESCALATED"},
        {"decided_at": None},
        {"decided_at": NOW - timedelta(days=1)},
    ],
)
def test_promotion_approval_missing_any_gate_element_raises(missing):
    gate = {**GATE_EVIDENCE, **missing}
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(
            _base(decision="PROMOTED", approved_by_policy=True, **gate)
        )


def test_promoted_decision_without_approval_raises():
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(
            _base(decision="PROMOTED", approved_by_policy=False, **GATE_EVIDENCE)
        )
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(_base(decision="PROMOTED", approved_by_policy=False))


@pytest.mark.parametrize("decision", ["WAIT_FOR_EVIDENCE", "REJECTED"])
def test_approval_requires_a_promoted_decision(decision):
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(
            _base(decision=decision, approved_by_policy=True, **GATE_EVIDENCE)
        )


def test_legacy_decision_without_gate_fields_still_validates():
    decision = PromotionDecision.model_validate(_base())
    assert decision.approved_by_policy is False
    assert decision.decision == "WAIT_FOR_EVIDENCE"
    assert decision.fortress_hypothesis_id is None
    assert decision.fortress_verdict_ref is None
    assert decision.council_name is None
    assert decision.council_verdict is None
    assert decision.decided_at is None


def test_rejected_decision_needs_no_gate_evidence():
    decision = PromotionDecision.model_validate(_base(decision="REJECTED"))
    assert decision.approved_by_policy is False
    assert decision.decision == "REJECTED"


@pytest.mark.parametrize("ref", ["xyz", "A" * 64, "a" * 63, "a" * 65, "g" * 64])
def test_fortress_verdict_ref_must_be_a_sha256_hex_digest(ref):
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(_base(fortress_verdict_ref=ref))


def test_evaluation_status_requires_provenance_for_completed_results():
    base = {
        "candidate_id": "candidate-1",
        "current_champion_id": "champion-1",
        "offline_eval": {"status": "PASS"},
        "replay_eval": PENDING,
        "adversarial_eval": PENDING,
        "shadow_eval": PENDING,
        "canary_eval": PENDING,
        "decision": "WAIT_FOR_EVIDENCE",
        "rollback_target": "champion-1",
        "created_at": NOW,
    }
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(base)
    base["offline_eval"] = {
        "status": "PASS",
        "evidence_refs": ["offline-report:sha256:abc"],
        "evaluated_at": NOW,
    }
    assert PromotionDecision.model_validate(base).offline_eval.status == "PASS"


def test_promotion_decision_schema_matches_runtime_model():
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/schemas/promotion-decision-v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema == promotion_decision_schema()
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["approved_by_policy"]["type"] == "boolean"
    assert "const" not in schema["properties"]["approved_by_policy"]
    assert set(schema["properties"]["decision"]["enum"]) == {
        "WAIT_FOR_EVIDENCE",
        "PROMOTED",
        "REJECTED",
    }
    verdict_ref = schema["properties"]["fortress_verdict_ref"]
    assert verdict_ref["anyOf"][0]["pattern"] == "^[a-f0-9]{64}$"


def test_completed_evaluation_references_are_nonempty_and_bounded():
    base = {
        "candidate_id": "candidate-1",
        "current_champion_id": "champion-1",
        "offline_eval": {
            "status": "PASS",
            "evidence_refs": ["x"],
            "evaluated_at": NOW,
        },
        "replay_eval": PENDING,
        "adversarial_eval": PENDING,
        "shadow_eval": PENDING,
        "canary_eval": PENDING,
        "decision": "WAIT_FOR_EVIDENCE",
        "rollback_target": "champion-1",
        "created_at": NOW,
    }
    base["offline_eval"]["evidence_refs"] = [""]
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(base)
    base["offline_eval"]["evidence_refs"] = ["x" * 513]
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(base)


def test_pending_evaluation_reason_must_be_nonempty():
    base = {
        "candidate_id": "candidate-1",
        "current_champion_id": None,
        "offline_eval": {"status": "NOT_RUN", "reason": ""},
        "replay_eval": PENDING,
        "adversarial_eval": PENDING,
        "shadow_eval": PENDING,
        "canary_eval": PENDING,
        "decision": "WAIT_FOR_EVIDENCE",
        "rollback_target": None,
        "created_at": NOW,
    }
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(base)

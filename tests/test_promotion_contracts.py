from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ai_quant_os.promotion_contracts import PromotionDecision, promotion_decision_schema

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PENDING = {"status": "NOT_RUN", "reason": "GATE_NOT_CONNECTED"}


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


def test_promotion_decision_rejects_approval_and_unknown_fields():
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
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate({**base, "approved_by_policy": True})
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate({**base, "unexpected": "accepted"})
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate({**base, "decision": "PROMOTE"})


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
    assert schema["properties"]["approved_by_policy"]["const"] is False
    assert schema["properties"]["decision"]["const"] == "WAIT_FOR_EVIDENCE"


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

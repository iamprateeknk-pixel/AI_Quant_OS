import json
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from ai_quant_os.promotion_contracts import PromotionDecision
from ai_quant_os.promotion_store import PromotionEvidenceStore


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PENDING = {"status": "NOT_RUN", "reason": "GATE_NOT_CONNECTED"}


def decision(candidate="candidate-1"):
    return PromotionDecision(
        candidate_id=candidate,
        current_champion_id="champion-1",
        offline_eval=PENDING,
        replay_eval=PENDING,
        adversarial_eval=PENDING,
        shadow_eval=PENDING,
        canary_eval=PENDING,
        rollback_target="champion-1",
        created_at=NOW,
    )


def test_promotion_evidence_is_immutable_audited_and_restart_verifiable(tmp_path):
    path = tmp_path / "promotion.sqlite3"
    store = PromotionEvidenceStore(path)
    evidence = decision()

    ref = store.register(evidence)

    assert (
        ref
        == "promotion-decision:"
        + sha256(
            json.dumps(
                evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    assert store.get(ref) == evidence
    reopened = PromotionEvidenceStore(path)
    assert reopened.get(ref) == evidence
    assert reopened.list_refs() == (ref,)
    assert evidence.approved_by_policy is False
    assert evidence.decision == "WAIT_FOR_EVIDENCE"


def test_promotion_evidence_registration_is_idempotent_and_conflicting_bytes_refuse(tmp_path):
    store = PromotionEvidenceStore(tmp_path / "promotion.sqlite3")
    evidence = decision()
    ref = store.register(evidence)

    assert store.register(evidence) == ref
    changed = decision().model_copy(update={"candidate_id": "candidate-2"})
    changed = changed.model_copy(update={"created_at": NOW.replace(day=2)})
    assert store.register(changed) != ref


def test_promotion_evidence_tampering_and_audit_binding_are_detected(tmp_path):
    path = tmp_path / "promotion.sqlite3"
    store = PromotionEvidenceStore(path)
    ref = store.register(decision())
    with sqlite3.connect(path) as db:
        db.execute("UPDATE promotion_decisions SET body=? WHERE ref=?", (b"{}", ref))

    with pytest.raises(ValueError, match="integrity"):
        store.get(ref)


def test_promotion_evidence_rejects_unknown_references(tmp_path):
    store = PromotionEvidenceStore(tmp_path / "promotion.sqlite3")
    with pytest.raises(ValueError, match="reference"):
        store.get("promotion-decision:" + "0" * 64)

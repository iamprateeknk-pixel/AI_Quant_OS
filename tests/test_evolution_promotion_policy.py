"""Signed promotion policy: promotion refuses without a valid signature."""

import base64
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ai_quant_os.evolution_evaluation import MetricComparison
from ai_quant_os.evolution_holdout import HoldoutEvaluation
from ai_quant_os.evolution_promotion_policy import (
    EvolutionPromotionGate,
    load_promotion_trust_store,
    load_signed_promotion_policy,
)

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
SIGNER_1 = Ed25519PrivateKey.generate()
SIGNER_2 = Ed25519PrivateKey.generate()
ROOT = Ed25519PrivateKey.generate()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _trust_store_raw(signers=(("release-signer-1", SIGNER_1), ("release-signer-2", SIGNER_2))):
    store = {
        "schema_version": "1.0",
        "signers": [
            {
                "signer_id": signer_id,
                "public_key": _b64u(key.public_key().public_bytes_raw()),
            }
            for signer_id, key in signers
        ],
    }
    return _canonical({"store": store, "signature": _b64u(ROOT.sign(_canonical(store)))})


def _trust_store():
    return load_promotion_trust_store(
        _trust_store_raw(), root_public_key=ROOT.public_key().public_bytes_raw()
    )


def _policy_payload(**changes):
    payload = {
        "schema_version": "1.0",
        "policy_id": "evolution-promotion",
        "policy_version": "1.0.0",
        "promotion_mode": "AUTOMATIC",
        "scope_agent_ids": ["A02", "A07"],
        "authorized_signer_ids": ["release-signer-1", "release-signer-2"],
        "required_signatures": 2,
        "minimum_holdout_cases": 1,
        "issued_at": "2026-10-01T00:00:00Z",
        "expires_at": "2027-10-01T00:00:00Z",
    }
    payload.update(changes)
    return payload


def _signed_policy(signers=(SIGNER_1, SIGNER_2), **changes):
    payload = _policy_payload(**changes)
    ids = ("release-signer-1", "release-signer-2")
    envelope = {
        "policy": payload,
        "signatures": [
            {"signer_id": ids[index], "signature": _b64u(key.sign(_canonical(payload)))}
            for index, key in enumerate(signers)
        ],
    }
    return _canonical(envelope)


def _holdout(*, verdict="HOLDOUT_PASS", challenger_ref="agent-genome:" + "a" * 64, case_count=4):
    comparison = MetricComparison(
        metric="CORRECTNESS",
        baseline_score=Decimal(0),
        challenger_score=Decimal(1),
        case_count=case_count,
        baseline_exact_matches=0,
        challenger_exact_matches=case_count,
        maximum_regression=Decimal(0),
        within_guardrail=True,
    )
    return HoldoutEvaluation(
        agent_id="A02",
        training_protocol_sha256="b" * 64,
        holdout_protocol_sha256="c" * 64,
        training_dataset_ref="evolution-dataset:" + "d" * 64,
        holdout_dataset_ref="evolution-dataset:" + "e" * 64,
        holdout_dataset_provenance="REVIEWED_REFERENCE",
        baseline_genome_ref="agent-genome:" + "f" * 64,
        challenger_genome_ref=challenger_ref,
        primary_metric="CORRECTNESS",
        baseline_score=Decimal(0),
        challenger_score=Decimal(1),
        case_count=case_count,
        baseline_exact_matches=0,
        challenger_exact_matches=case_count,
        minimum_improvement=Decimal("0.01"),
        minimum_improvement_met=True,
        metrics=(comparison,),
        verdict=verdict,
        evaluated_at=NOW,
    )


def _gate():
    return EvolutionPromotionGate(trust_store=_trust_store())


def test_trust_store_rejects_a_forged_root_signature():
    raw = bytearray(_trust_store_raw())
    raw[-10] ^= 1
    with pytest.raises(ValueError, match="trust"):
        load_promotion_trust_store(bytes(raw), root_public_key=ROOT.public_key().public_bytes_raw())


def test_signed_policy_verifies_with_full_quorum():
    policy, verification = load_signed_promotion_policy(
        _signed_policy(), trust_store=_trust_store()
    )
    assert policy.policy_id == "evolution-promotion"
    assert verification.state == "POLICY_SIGNATURES_VERIFIED"
    assert verification.signer_ids == ("release-signer-1", "release-signer-2")


def test_policy_with_a_missing_quorum_signature_is_invalid():
    with pytest.raises(ValueError, match="quorum"):
        load_signed_promotion_policy(
            _signed_policy(signers=(SIGNER_1,)), trust_store=_trust_store()
        )


def test_policy_signed_by_an_untrusted_key_is_invalid():
    outsider = Ed25519PrivateKey.generate()
    raw = _signed_policy(signers=(SIGNER_1, outsider))
    with pytest.raises(ValueError, match="signature is invalid"):
        load_signed_promotion_policy(raw, trust_store=_trust_store())


def test_gate_refuses_when_no_signed_policy_exists():
    decision = _gate().decide(
        policy_raw=None,
        holdout=_holdout(),
        challenger_genome_ref="agent-genome:" + "a" * 64,
        agent_id="A02",
        now=NOW,
    )
    assert decision.decision == "REFUSED"
    assert decision.reasons == ("SIGNED_POLICY_MISSING",)
    assert decision.promotion_applied is False


def test_gate_refuses_a_tampered_policy():
    raw = bytearray(_signed_policy())
    raw[40] ^= 1
    decision = _gate().decide(
        policy_raw=bytes(raw),
        holdout=_holdout(),
        challenger_genome_ref="agent-genome:" + "a" * 64,
        agent_id="A02",
        now=NOW,
    )
    assert decision.decision == "REFUSED"
    assert decision.reasons == ("SIGNED_POLICY_INVALID",)


def test_gate_refuses_an_expired_policy():
    decision = _gate().decide(
        policy_raw=_signed_policy(expires_at="2026-10-02T00:00:00Z"),
        holdout=_holdout(),
        challenger_genome_ref="agent-genome:" + "a" * 64,
        agent_id="A02",
        now=NOW,
    )
    assert decision.decision == "REFUSED"
    assert decision.reasons == ("SIGNED_POLICY_OUTSIDE_VALIDITY_WINDOW",)


def test_gate_refuses_an_agent_outside_the_policy_scope():
    decision = _gate().decide(
        policy_raw=_signed_policy(),
        holdout=_holdout(),
        challenger_genome_ref="agent-genome:" + "a" * 64,
        agent_id="A03",
        now=NOW,
    )
    assert decision.decision == "REFUSED"
    assert decision.reasons == ("SIGNED_POLICY_SCOPE_EXCLUDES_AGENT",)


def test_gate_refuses_when_holdout_did_not_pass():
    decision = _gate().decide(
        policy_raw=_signed_policy(),
        holdout=_holdout(verdict="HOLDOUT_FAIL"),
        challenger_genome_ref="agent-genome:" + "a" * 64,
        agent_id="A02",
        now=NOW,
    )
    assert decision.decision == "REFUSED"
    assert "HOLDOUT_VERDICT_NOT_PASS" in decision.reasons


def test_gate_refuses_when_holdout_covers_a_different_challenger():
    decision = _gate().decide(
        policy_raw=_signed_policy(),
        holdout=_holdout(challenger_ref="agent-genome:" + "9" * 64),
        challenger_genome_ref="agent-genome:" + "a" * 64,
        agent_id="A02",
        now=NOW,
    )
    assert decision.decision == "REFUSED"
    assert "HOLDOUT_CHALLENGER_MISMATCH" in decision.reasons


def test_gate_refuses_when_holdout_is_below_the_policy_case_floor():
    decision = _gate().decide(
        policy_raw=_signed_policy(minimum_holdout_cases=8),
        holdout=_holdout(case_count=4),
        challenger_genome_ref="agent-genome:" + "a" * 64,
        agent_id="A02",
        now=NOW,
    )
    assert decision.decision == "REFUSED"
    assert "HOLDOUT_CASES_BELOW_POLICY_MINIMUM" in decision.reasons


def test_gate_authorizes_only_with_valid_policy_and_passing_holdout():
    decision = _gate().decide(
        policy_raw=_signed_policy(),
        holdout=_holdout(),
        challenger_genome_ref="agent-genome:" + "a" * 64,
        agent_id="A02",
        now=NOW,
    )
    assert decision.decision == "AUTHORIZED"
    assert decision.reasons == ()
    assert decision.policy_sha256 is not None
    assert decision.holdout_evaluation_ref is not None
    # Authorization is not application: the served version never flips here.
    assert decision.promotion_applied is False

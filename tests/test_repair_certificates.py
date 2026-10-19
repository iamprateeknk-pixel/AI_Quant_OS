import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import validate


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _snapshot(*, new_version="0.2.1", old_version="0.2.0"):
    from ai_quant_os.repair_snapshots import RepairSourceFile, RepairSourceSnapshot, canonical_json

    manifest = {"agent_id": "A02", "agent_version": new_version}
    incident = {"incident_id": "a" * 64, "agent_version": old_version}
    return RepairSourceSnapshot(
        candidate_id="b" * 64,
        candidate_commit="c" * 40,
        rollback_commit="d" * 40,
        manifest_json=canonical_json(manifest),
        incident_json=canonical_json(incident),
        files=(RepairSourceFile("src/ai_quant_os/regime_agent.py", b"candidate source"),),
    )


def _signed_certificate(snapshot, key, *, changes=None):
    certificate = {
        "schema_version": "1.0",
        "verifier_id": "independent-reviewer-1",
        "verifier_source_sha256": "e" * 64,
        "suite_sha256": "f" * 64,
        "image_id": "sha256:" + "1" * 64,
        "candidate_id": snapshot.candidate_id,
        "candidate_commit": snapshot.candidate_commit,
        "rollback_commit": snapshot.rollback_commit,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "request_sha256": "a" * 64,
        "incident_id": snapshot.incident["incident_id"],
        "agent_id": "A02",
        "old_version": snapshot.incident["agent_version"],
        "new_version": snapshot.proposed_manifest["agent_version"],
        "replay_sha256": "2" * 64,
        "issued_at": "2026-10-14T12:30:00Z",
        "verdict": "PASS",
        "test_results": [
            {"test_id": "incident-replay", "status": "PASS", "evidence_sha256": "3" * 64},
            {"test_id": "regression-contract", "status": "PASS", "evidence_sha256": "4" * 64},
        ],
        "promotion_allowed": False,
    }
    certificate.update(changes or {})
    payload = _canonical(certificate)
    envelope = {
        "certificate": certificate,
        "signature": base64.urlsafe_b64encode(key.sign(payload)).decode().rstrip("="),
    }
    return _canonical(envelope)


def _signed_trust_store(root_key, reviewer_key, *, changes=None):
    store = {
        "schema_version": "1.0",
        "signers": [
            {
                "verifier_id": "independent-reviewer-1",
                "public_key": base64.urlsafe_b64encode(reviewer_key.public_key().public_bytes_raw())
                .decode()
                .rstrip("="),
                "verifier_source_sha256": "e" * 64,
            }
        ],
    }
    store.update(changes or {})
    return _canonical(
        {
            "store": store,
            "signature": base64.urlsafe_b64encode(root_key.sign(_canonical(store)))
            .decode()
            .rstrip("="),
        }
    )


def _verify(raw, snapshot, key):
    from ai_quant_os.repair_certificates import verify_repair_certificate

    return verify_repair_certificate(
        snapshot,
        raw,
        trusted_public_keys={
            "independent-reviewer-1": key.public_key().public_bytes_raw(),
        },
        replay_sha256="2" * 64,
        suite_sha256="f" * 64,
        request_sha256="a" * 64,
        incident_id=snapshot.incident["incident_id"],
        verifier_source_sha256="e" * 64,
        image_id="sha256:" + "1" * 64,
    )


def test_signed_certificate_binds_passed_verification_to_exact_snapshot_and_replay():
    snapshot = _snapshot()
    key = Ed25519PrivateKey.generate()
    raw = _signed_certificate(snapshot, key)
    schema = json.loads(
        (
            Path(__file__).parents[1] / "docs/schemas/repair-verification-certificate.schema.json"
        ).read_text(encoding="utf-8")
    )
    validate(json.loads(raw), schema)

    result = _verify(raw, snapshot, key)

    assert result.state == "INDEPENDENT_VERIFICATION_PASSED"
    assert result.snapshot_sha256 == snapshot.snapshot_sha256
    assert result.promotion_allowed is False


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_sha256": "5" * 64},
        {"request_sha256": "5" * 64},
        {"candidate_commit": "6" * 40},
        {"incident_id": "7" * 64},
        {"replay_sha256": "8" * 64},
        {"suite_sha256": "9" * 64},
        {"verifier_source_sha256": "a" * 64},
        {"image_id": "sha256:" + "b" * 64},
    ],
)
def test_certificate_with_wrong_snapshot_or_verifier_binding_is_refused(changes):
    snapshot = _snapshot()
    key = Ed25519PrivateKey.generate()

    with pytest.raises(ValueError):
        _verify(_signed_certificate(snapshot, key, changes=changes), snapshot, key)


def test_certificate_cannot_be_changed_after_independent_signature():
    snapshot = _snapshot()
    key = Ed25519PrivateKey.generate()
    raw = json.loads(_signed_certificate(snapshot, key))
    raw["certificate"]["verdict"] = "FAIL"
    raw["certificate"]["promotion_allowed"] = True

    with pytest.raises(ValueError):
        _verify(_canonical(raw), snapshot, key)


def test_failure_certificate_is_recorded_as_review_only_not_as_pass():
    snapshot = _snapshot()
    key = Ed25519PrivateKey.generate()
    raw = _signed_certificate(
        snapshot,
        key,
        changes={
            "verdict": "FAIL",
            "test_results": [
                {"test_id": "incident-replay", "status": "FAIL", "evidence_sha256": "3" * 64},
                {"test_id": "regression-contract", "status": "PASS", "evidence_sha256": "4" * 64},
            ],
        },
    )

    result = _verify(raw, snapshot, key)

    assert result.state == "REPAIR_FAILED_AWAITING_REVIEW"
    assert result.promotion_allowed is False


@pytest.mark.parametrize("version", ["0.2.0", "0.1.9", "invalid"])
def test_certificate_cannot_attest_an_unchanged_or_older_repair_version(version):
    snapshot = _snapshot(new_version=version)
    key = Ed25519PrivateKey.generate()

    with pytest.raises(ValueError):
        _verify(_signed_certificate(snapshot, key), snapshot, key)


def test_untrusted_key_duplicate_fields_and_unknown_authority_fields_are_refused():
    snapshot = _snapshot()
    key = Ed25519PrivateKey.generate()
    raw = _signed_certificate(snapshot, key)

    with pytest.raises(ValueError):
        from ai_quant_os.repair_certificates import verify_repair_certificate

        verify_repair_certificate(
            snapshot,
            raw,
            trusted_public_keys={},
            replay_sha256="2" * 64,
            suite_sha256="f" * 64,
            request_sha256="a" * 64,
            incident_id=snapshot.incident["incident_id"],
            verifier_source_sha256="e" * 64,
            image_id="sha256:" + "1" * 64,
        )

    duplicate = b'{"certificate":{},"certificate":{},"signature":"x"}'
    with pytest.raises(ValueError):
        _verify(duplicate, snapshot, key)

    unknown = _signed_certificate(snapshot, key, changes={"execution_verified": True})
    with pytest.raises(ValueError):
        _verify(unknown, snapshot, key)


def test_trust_store_requires_a_separate_root_signature_and_returns_exact_pins():
    from ai_quant_os.repair_certificates import load_repair_verifier_trust_store

    root_key = Ed25519PrivateKey.generate()
    reviewer_key = Ed25519PrivateKey.generate()

    raw = _signed_trust_store(root_key, reviewer_key)
    schema = json.loads(
        (
            Path(__file__).parents[1] / "docs/schemas/repair-verifier-trust-store.schema.json"
        ).read_text(encoding="utf-8")
    )
    validate(json.loads(raw), schema)
    trust = load_repair_verifier_trust_store(
        raw,
        root_public_key=root_key.public_key().public_bytes_raw(),
    )

    assert trust.trusted_public_keys["independent-reviewer-1"] == (
        reviewer_key.public_key().public_bytes_raw()
    )
    assert trust.verifier_source_sha256["independent-reviewer-1"] == "e" * 64


def test_trust_store_refuses_signature_tampering_untrusted_root_and_duplicate_signers():
    from ai_quant_os.repair_certificates import load_repair_verifier_trust_store

    root_key = Ed25519PrivateKey.generate()
    reviewer_key = Ed25519PrivateKey.generate()
    wrong_root = Ed25519PrivateKey.generate()
    raw = _signed_trust_store(root_key, reviewer_key)
    tampered = json.loads(raw)
    tampered["store"]["signers"][0]["verifier_source_sha256"] = "a" * 64

    with pytest.raises(ValueError):
        load_repair_verifier_trust_store(
            _canonical(tampered), root_public_key=root_key.public_key().public_bytes_raw()
        )
    with pytest.raises(ValueError):
        load_repair_verifier_trust_store(
            raw, root_public_key=wrong_root.public_key().public_bytes_raw()
        )

    duplicated = {
        "schema_version": "1.0",
        "signers": [
            {
                "verifier_id": "independent-reviewer-1",
                "public_key": base64.urlsafe_b64encode(reviewer_key.public_key().public_bytes_raw())
                .decode()
                .rstrip("="),
                "verifier_source_sha256": "e" * 64,
            }
        ]
        * 2,
    }
    duplicated_raw = _canonical(
        {
            "store": duplicated,
            "signature": base64.urlsafe_b64encode(root_key.sign(_canonical(duplicated)))
            .decode()
            .rstrip("="),
        }
    )
    with pytest.raises(ValueError):
        load_repair_verifier_trust_store(
            duplicated_raw, root_public_key=root_key.public_key().public_bytes_raw()
        )

"""Repair trials: shadow -> canary -> enforced activation, with rollback."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.repair_certificates import verify_repair_certificate
from ai_quant_os.repair_trials import RepairTrialStore, ShadowVerdict
from test_repair_certificates import _signed_certificate, _snapshot

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
REPLAY_SHA256 = "2" * 64


def _verification(snapshot, *, verdict="PASS"):
    key = Ed25519PrivateKey.generate()
    changes = {"verdict": verdict}
    if verdict == "FAIL":
        changes["test_results"] = [
            {"test_id": "incident-replay", "status": "FAIL", "evidence_sha256": "3" * 64}
        ]
    raw = _signed_certificate(snapshot, key, changes=changes)
    return verify_repair_certificate(
        snapshot,
        raw,
        trusted_public_keys={"independent-reviewer-1": key.public_key().public_bytes_raw()},
        replay_sha256=REPLAY_SHA256,
        suite_sha256="f" * 64,
        request_sha256="a" * 64,
        incident_id=snapshot.incident["incident_id"],
        verifier_source_sha256="e" * 64,
        image_id="sha256:" + "1" * 64,
    )


def _open_trial(tmp_path):
    store = RepairTrialStore(tmp_path / "trials.sqlite3")
    snapshot = _snapshot()
    trial = store.create_trial(
        snapshot, _verification(snapshot), replay_sha256=REPLAY_SHA256, created_at=NOW
    )
    return store, snapshot, trial


def _passing_evaluator(record):
    return ShadowVerdict(
        evaluator_id="shadow-replay-evaluator",
        passed=True,
        replay_sha256=record.replay_sha256,
        evidence_refs=("shadow-evidence:" + "5" * 64,),
        summary="Candidate matched incumbent behaviour on the incident replay.",
    )


def _through_canary_passed(tmp_path):
    store, snapshot, trial = _open_trial(tmp_path)
    store.evaluate_shadow(trial.trial_id, _passing_evaluator, evaluated_at=NOW)
    store.start_canary(
        trial.trial_id, scope="agent:A02", entered_at=NOW, min_soak_seconds=300
    )
    passed = store.evaluate_canary(
        trial.trial_id, now=NOW + timedelta(seconds=301), incidents=()
    )
    assert passed.state == "CANARY_PASSED"
    return store, snapshot, passed


def test_trial_opens_only_after_independent_verification_passes(tmp_path):
    store = RepairTrialStore(tmp_path / "trials.sqlite3")
    snapshot = _snapshot()
    failed = _verification(snapshot, verdict="FAIL")
    assert failed.state == "REPAIR_FAILED_AWAITING_REVIEW"
    with pytest.raises(ValueError, match="independent verification"):
        store.create_trial(snapshot, failed, replay_sha256=REPLAY_SHA256, created_at=NOW)
    verification = _verification(snapshot)
    trial = store.create_trial(
        snapshot, verification, replay_sha256=REPLAY_SHA256, created_at=NOW
    )
    assert trial.state == "SHADOW_PENDING"
    assert trial.agent_id == "A02"
    assert trial.incident_id == snapshot.incident["incident_id"]
    assert trial.promotion_allowed is False
    again = store.create_trial(
        snapshot, verification, replay_sha256=REPLAY_SHA256, created_at=NOW
    )
    assert again.trial_id == trial.trial_id


def test_shadow_stage_fails_closed_without_an_evaluator(tmp_path):
    store, _, trial = _open_trial(tmp_path)
    with pytest.raises(ValueError, match="injected independent evaluator"):
        store.evaluate_shadow(trial.trial_id, None, evaluated_at=NOW)
    assert store.get(trial.trial_id).state == "SHADOW_PENDING"


def test_shadow_verdict_must_bind_the_trial_replay(tmp_path):
    store, _, trial = _open_trial(tmp_path)

    def wrong_replay(record):
        return ShadowVerdict(
            evaluator_id="shadow-replay-evaluator",
            passed=True,
            replay_sha256="9" * 64,
            evidence_refs=("shadow-evidence:" + "5" * 64,),
            summary="Bound to a different replay.",
        )

    with pytest.raises(ValueError, match="incident replay"):
        store.evaluate_shadow(trial.trial_id, wrong_replay, evaluated_at=NOW)
    assert store.get(trial.trial_id).state == "SHADOW_PENDING"


def test_failed_shadow_never_reaches_canary(tmp_path):
    store, _, trial = _open_trial(tmp_path)

    def failing(record):
        return ShadowVerdict(
            evaluator_id="shadow-replay-evaluator",
            passed=False,
            replay_sha256=record.replay_sha256,
            evidence_refs=("shadow-evidence:" + "6" * 64,),
            summary="Candidate diverged from incumbent behaviour in shadow.",
        )

    failed = store.evaluate_shadow(trial.trial_id, failing, evaluated_at=NOW)
    assert failed.state == "SHADOW_FAILED"
    with pytest.raises(ValueError, match="cannot accept this transition"):
        store.start_canary(trial.trial_id, scope="agent:A02", entered_at=NOW)


def test_canary_soak_then_activation_authorization_and_closure(tmp_path):
    store, snapshot, trial = _through_canary_passed(tmp_path)
    with pytest.raises(ValueError, match="activation-authorized"):
        store.close_incident(trial.trial_id, closed_at=NOW + timedelta(seconds=400))
    authorized = store.authorize_activation(
        trial.trial_id, operator_ref="operator:maintainer-1", authorized_at=NOW + timedelta(seconds=400)
    )
    assert authorized.state == "ACTIVATION_AUTHORIZED"
    assert authorized.activation is not None
    # Authorization never applies the version flip inside the trial store.
    assert authorized.activation.applied is False
    closure = store.close_incident(trial.trial_id, closed_at=NOW + timedelta(seconds=500))
    assert closure.state == "CLOSED_REPAIR_ACTIVATED"
    assert closure.incident_id == snapshot.incident["incident_id"]
    assert closure.candidate_id == snapshot.candidate_id
    assert closure.trial_id == trial.trial_id
    assert closure.certificate_sha256 == trial.certificate_sha256
    assert closure.verifier_id == "independent-reviewer-1"
    assert closure.verifier_verdict == "PASS"
    assert closure.shadow_evidence_refs == ("shadow-evidence:" + "5" * 64,)
    assert closure.canary_soak_seconds >= 300
    assert closure.incidents_during_canary == 0
    assert closure.journal_writeback is False
    assert closure.closure_ref.startswith("repair-closure:")
    assert store.closure_for(closure.incident_id) == closure


def test_canary_stays_active_until_the_soak_elapses(tmp_path):
    store, _, trial = _open_trial(tmp_path)
    store.evaluate_shadow(trial.trial_id, _passing_evaluator, evaluated_at=NOW)
    store.start_canary(trial.trial_id, scope="agent:A02", entered_at=NOW, min_soak_seconds=300)
    early = store.evaluate_canary(trial.trial_id, now=NOW + timedelta(seconds=60), incidents=())
    assert early.state == "CANARY_ACTIVE"


def test_incident_during_canary_rolls_the_trial_back(tmp_path):
    store, _, trial = _open_trial(tmp_path)
    store.evaluate_shadow(trial.trial_id, _passing_evaluator, evaluated_at=NOW)
    store.start_canary(trial.trial_id, scope="agent:A02", entered_at=NOW, min_soak_seconds=300)
    incident = HealthIncident(
        "AGENT_FAILED",
        "agent-A02",
        "{}",
        NOW + timedelta(seconds=30),
        agent_id="A02",
        agent_version="0.2.1",
        manifest_ref="agent-manifest:" + "a" * 64,
    )
    rolled = store.evaluate_canary(
        trial.trial_id, now=NOW + timedelta(seconds=400), incidents=(incident,)
    )
    assert rolled.state == "ROLLED_BACK"
    assert rolled.rollback is not None
    assert rolled.rollback.reason == "INCIDENT_DURING_CANARY"
    assert rolled.rollback.rollback_commit == trial.rollback_commit
    with pytest.raises(ValueError, match="activation-authorized"):
        store.close_incident(trial.trial_id, closed_at=NOW + timedelta(seconds=500))
    with pytest.raises(ValueError, match="cannot accept this transition"):
        store.authorize_activation(
            trial.trial_id, operator_ref="operator:maintainer-1", authorized_at=NOW
        )


def test_incident_for_another_agent_does_not_roll_back(tmp_path):
    store, _, trial = _open_trial(tmp_path)
    store.evaluate_shadow(trial.trial_id, _passing_evaluator, evaluated_at=NOW)
    store.start_canary(trial.trial_id, scope="agent:A02", entered_at=NOW, min_soak_seconds=300)
    other = HealthIncident(
        "AGENT_FAILED",
        "agent-A07",
        "{}",
        NOW + timedelta(seconds=30),
        agent_id="A07",
        agent_version="0.2.0",
        manifest_ref="agent-manifest:" + "b" * 64,
    )
    result = store.evaluate_canary(
        trial.trial_id, now=NOW + timedelta(seconds=400), incidents=(other,)
    )
    assert result.state == "CANARY_PASSED"


def test_manual_rollback_from_canary_passed(tmp_path):
    store, _, trial = _through_canary_passed(tmp_path)
    rolled = store.rollback(
        trial.trial_id, reason="OPERATOR_ABORT", at=NOW + timedelta(seconds=600)
    )
    assert rolled.state == "ROLLED_BACK"
    assert rolled.rollback is not None
    assert rolled.rollback.reason == "OPERATOR_ABORT"


def test_trial_records_survive_reopen_and_tampering_is_refused(tmp_path):
    store, _, trial = _through_canary_passed(tmp_path)
    reopened = RepairTrialStore(tmp_path / "trials.sqlite3")
    assert reopened.get(trial.trial_id).state == "CANARY_PASSED"
    db = sqlite3.connect(tmp_path / "trials.sqlite3")
    db.execute(
        "UPDATE repair_trials SET record_json = ? WHERE trial_id = ?",
        ('{"tampered":true}', trial.trial_id),
    )
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="integrity"):
        RepairTrialStore(tmp_path / "trials.sqlite3")

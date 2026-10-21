import pytest

from ai_quant_os.recovery_verifier import (
    QUORUM_GATES,
    TRANSITIONS,
    RecoveryState,
    VerificationReport,
    advance,
    verify_recovery,
)


ALL_GATES = {gate: True for gate in QUORUM_GATES}
EVIDENCE = ("incident-snapshot-1", "replay-report-1")


# --- state machine ------------------------------------------------------------


def test_full_recovery_path_is_legal_end_to_end():
    path = (
        RecoveryState.FAULT_DETECTED,
        RecoveryState.NO_NEW_RISK,
        RecoveryState.STATE_SNAPSHOT,
        RecoveryState.AGENT_QUARANTINED,
        RecoveryState.DIAGNOSE,
        RecoveryState.REPAIR_CANDIDATE,
        RecoveryState.TEST_REPLAY_EVAL,
        RecoveryState.SHADOW_CANARY,
        RecoveryState.RECOVERY_VERIFIED,
        RecoveryState.REDUCED_RISK,
        RecoveryState.NORMAL,
    )
    current = RecoveryState.NORMAL
    for target in path:
        current = advance(current, target)
    assert current is RecoveryState.NORMAL


def test_halt_also_recovers_only_through_the_full_path():
    assert advance(RecoveryState.FAULT_DETECTED, RecoveryState.HALT) is RecoveryState.HALT
    assert advance(RecoveryState.NO_NEW_RISK, RecoveryState.HALT) is RecoveryState.HALT
    assert advance(RecoveryState.HALT, RecoveryState.STATE_SNAPSHOT) is (
        RecoveryState.STATE_SNAPSHOT
    )


def test_no_frozen_state_can_jump_directly_to_normal():
    for frozen in (RecoveryState.NO_NEW_RISK, RecoveryState.HALT):
        with pytest.raises(ValueError):
            advance(frozen, RecoveryState.NORMAL)
    # Only REDUCED_RISK may return the platform to NORMAL.
    for state, targets in TRANSITIONS.items():
        if RecoveryState.NORMAL in targets:
            assert state is RecoveryState.REDUCED_RISK


def test_verified_is_not_normal_and_reduced_risk_can_refreeze():
    with pytest.raises(ValueError):
        advance(RecoveryState.RECOVERY_VERIFIED, RecoveryState.NORMAL)
    assert advance(RecoveryState.RECOVERY_VERIFIED, RecoveryState.REDUCED_RISK) is (
        RecoveryState.REDUCED_RISK
    )
    assert advance(RecoveryState.REDUCED_RISK, RecoveryState.NO_NEW_RISK) is (
        RecoveryState.NO_NEW_RISK
    )


def test_illegal_jumps_raise():
    with pytest.raises(ValueError):
        advance(RecoveryState.NORMAL, RecoveryState.NO_NEW_RISK)
    with pytest.raises(ValueError):
        advance(RecoveryState.NORMAL, RecoveryState.NORMAL)
    with pytest.raises(ValueError):
        advance(RecoveryState.AGENT_QUARANTINED, RecoveryState.SHADOW_CANARY)
    with pytest.raises(ValueError):
        advance(RecoveryState.DIAGNOSE, RecoveryState.NORMAL)


def test_failure_loops_go_back_not_forward():
    assert advance(RecoveryState.TEST_REPLAY_EVAL, RecoveryState.DIAGNOSE) is (
        RecoveryState.DIAGNOSE
    )
    assert advance(RecoveryState.SHADOW_CANARY, RecoveryState.AGENT_QUARANTINED) is (
        RecoveryState.AGENT_QUARANTINED
    )


# --- quorum verification --------------------------------------------------------


def test_quorum_gates_are_the_eight_blueprint_gates():
    assert QUORUM_GATES == (
        "agent_health",
        "repair_verification",
        "data_state",
        "risk_execution",
        "broker_truth",
        "security",
        "canary",
        "resume_controller",
    )


def test_verified_only_when_all_gates_pass_with_replay_and_evidence():
    report = verify_recovery(
        gate_results=dict(ALL_GATES),
        evidence_refs=EVIDENCE,
        independent_replay_passed=True,
    )
    assert report.verdict == "VERIFIED"
    assert report.failed_gates == ()
    assert report.evidence_refs == EVIDENCE


def test_false_or_missing_gate_is_rejected_and_named():
    gates = dict(ALL_GATES)
    gates["broker_truth"] = False
    del gates["security"]
    report = verify_recovery(
        gate_results=gates,
        evidence_refs=EVIDENCE,
        independent_replay_passed=True,
    )
    assert report.verdict == "REJECTED"
    assert "broker_truth" in report.failed_gates
    assert "security" in report.failed_gates


def test_self_assessment_without_independent_replay_is_rejected():
    # Every gate claimed True by the repair side is not enough: the verifier
    # is independent and requires its own incident replay to have passed.
    report = verify_recovery(
        gate_results=dict(ALL_GATES),
        evidence_refs=EVIDENCE,
        independent_replay_passed=False,
    )
    assert report.verdict == "REJECTED"
    assert report.failed_gates == ("independent_replay",)


def test_missing_evidence_is_rejected():
    report = verify_recovery(
        gate_results=dict(ALL_GATES),
        evidence_refs=(),
        independent_replay_passed=True,
    )
    assert report.verdict == "REJECTED"
    assert "evidence" in report.failed_gates


def test_unknown_gate_names_do_not_satisfy_the_quorum():
    gates = {f"gate-{index}": True for index in range(8)}
    report = verify_recovery(
        gate_results=gates,
        evidence_refs=EVIDENCE,
        independent_replay_passed=True,
    )
    assert report.verdict == "REJECTED"
    assert set(report.failed_gates) == set(QUORUM_GATES)


def test_report_rejects_unknown_verdict():
    with pytest.raises(ValueError):
        VerificationReport(verdict="MAYBE", failed_gates=(), evidence_refs=EVIDENCE)

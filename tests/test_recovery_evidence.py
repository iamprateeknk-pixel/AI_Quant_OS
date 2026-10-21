"""Recovery quorum evidence production (Phase 3, recovery_verifier wiring).

The producer in ``ai_quant_os.recovery_evidence`` must compute every gate
from the live paper system in the call -- a gate marked True without
computed evidence is a failure of these tests by construction:

(a) a healthy paper system (completed triage, reconciled ledger, on-disk
    DB) yields every gate True except ``canary`` and a genuinely-run
    independent replay, so the verifier's only failure is ``canary``;
(b) a ledger that does not reconcile fails ``broker_truth``, and a
    tampered audit chain fails ``data_state`` and the replay;
(c) with no on-disk DB the replay fails closed, and a system whose state
    cannot be read at all produces no evidence refs, so the verifier's
    ``evidence`` pseudo-gate fails too;
(d) end to end, an accepted recovery on a healthy on-disk app records
    the produced evidence on the receipt (mode still SHADOW, verdict
    REJECTED naming only ``canary``), idempotent replay returns the
    identical receipt, and refusals are unchanged.
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.execution import PaperExecutionGateway
from ai_quant_os.health_store import HealthStore
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.portfolio import PaperFill
from ai_quant_os.recovery_evidence import produce_recovery_evidence
from ai_quant_os.recovery_verifier import verify_recovery
from ai_quant_os.risk import RiskKernel, RiskLimits
from ai_quant_os.risk_policy import DEFAULT_POLICY
from test_paper_recovery import recovery_fixture

OPERATOR = "operator-secret"
RECOVERY = "recovery-secret"
RECOVER_URL = "/api/v1/control/recover-paper"
REASON = "feed restored and paper ledger checked"
SHA256_REF = re.compile(r"sha256:[0-9a-f]{64}$")


def _healthy_components(tmp_path):
    """Paper components after a real contained + reconciled triage."""
    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    order_id = execution.orders[0].order_id
    service.contain("incident-1", order_id)
    result = service.reconcile("incident-1", order_id, 1)
    assert result["stage"] == "REVIEW_REQUIRED"
    assert result["positions_match"] is True
    return {
        "service": service,
        "execution": execution,
        "risk": risk,
        "state": state,
        "broker": broker,
        "order_id": order_id,
        "health_store": HealthStore(tmp_path / "health.sqlite3"),
        "paper_db_path": tmp_path / "paper.sqlite3",
    }


def _produce(components, **overrides):
    kwargs = {
        "state_store": components["state"],
        "execution": components["execution"],
        "risk": components["risk"],
        "health_store": components["health_store"],
        "paper_db_path": components["paper_db_path"],
        "receipt_id": "evidence-test-receipt",
        "security_verified": True,
        "risk_policy": DEFAULT_POLICY,
    }
    kwargs.update(overrides)
    return produce_recovery_evidence(**kwargs)


def _report(evidence):
    return verify_recovery(
        gate_results=evidence.gate_results,
        evidence_refs=evidence.evidence_refs,
        independent_replay_passed=evidence.independent_replay_passed,
    )


# --- (a) healthy system -------------------------------------------------------


def test_healthy_system_produces_all_gates_except_canary(tmp_path):
    components = _healthy_components(tmp_path)
    evidence = _produce(components)
    assert evidence.gate_results == {
        "agent_health": True,
        "repair_verification": True,
        "data_state": True,
        "risk_execution": True,
        "broker_truth": True,
        "security": True,
        "canary": False,
        "resume_controller": True,
    }
    assert evidence.independent_replay_passed is True
    assert evidence.evidence_refs
    hashed = [ref for ref in evidence.evidence_refs if SHA256_REF.search(ref)]
    # Snapshot, audit head, triage, policy, health, replay: real hashes
    # computed from the live system in this call.
    assert len(hashed) >= 6
    assert any(ref.startswith("paper-state-snapshot:sha256:") for ref in hashed)
    assert any(ref.startswith("independent-replay:sha256:") for ref in hashed)
    report = _report(evidence)
    assert report.verdict == "REJECTED"
    assert report.failed_gates == ("canary",)


def test_security_gate_is_never_defaulted_by_the_producer(tmp_path):
    components = _healthy_components(tmp_path)
    evidence = _produce(components, security_verified=False)
    assert evidence.gate_results["security"] is False
    assert "security" in _report(evidence).failed_gates


def test_repair_verification_requires_a_triage_artifact(tmp_path):
    # Same healthy components but no contained/reconciled incident: the
    # triage precondition is vacuously complete, yet there is no repair
    # artifact to verify, so the gate must stay False.
    _, execution, risk, state, _ = recovery_fixture(tmp_path)
    evidence = produce_recovery_evidence(
        state_store=state,
        execution=execution,
        risk=risk,
        health_store=HealthStore(tmp_path / "health.sqlite3"),
        paper_db_path=tmp_path / "paper.sqlite3",
        receipt_id="no-triage-receipt",
        security_verified=True,
        risk_policy=DEFAULT_POLICY,
    )
    assert evidence.gate_results["repair_verification"] is False
    assert evidence.gate_results["data_state"] is True


# --- (b) tampering / mismatch ---------------------------------------------------


def test_unreconciled_broker_fill_fails_broker_truth(tmp_path):
    components = _healthy_components(tmp_path)
    order = components["execution"].orders[0]
    components["broker"].report_fill(
        order.command,
        PaperFill(
            fill_id="unimported",
            order_id=order.order_id,
            instrument_id="EURUSD",
            side="BUY",
            quantity=Decimal(100),
            price=Decimal("1.1001"),
            fee=Decimal(0),
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source="PAPER_BROKER",
        ),
    )
    evidence = _produce(components)
    assert evidence.gate_results["broker_truth"] is False
    failed = _report(evidence).failed_gates
    assert "broker_truth" in failed
    assert "canary" in failed


def test_tampered_audit_chain_fails_data_state_and_replay(tmp_path):
    components = _healthy_components(tmp_path)
    path = components["paper_db_path"]
    connection = sqlite3.connect(str(path))
    try:
        key = connection.execute("SELECT event_key FROM audit_events LIMIT 1").fetchone()[0]
        connection.execute(
            "UPDATE audit_events SET event_json = ? WHERE event_key = ?",
            ('{"action": "TAMPERED"}', key),
        )
        connection.commit()
    finally:
        connection.close()
    evidence = _produce(components)
    assert evidence.gate_results["data_state"] is False
    assert evidence.independent_replay_passed is False
    failed = _report(evidence).failed_gates
    assert "data_state" in failed
    assert "independent_replay" in failed


# --- (c) fail-closed ------------------------------------------------------------


def test_no_db_path_fails_replay_closed(tmp_path):
    components = _healthy_components(tmp_path)
    evidence = _produce(components, paper_db_path=None)
    assert evidence.independent_replay_passed is False
    assert "independent_replay" in _report(evidence).failed_gates


def test_unreadable_state_produces_no_evidence(tmp_path):
    state = PaperStateStore(None)
    risk = RiskKernel(RiskLimits(Decimal(1000), Decimal(1500), 5))
    execution = PaperExecutionGateway(risk, store_path=None)
    state._connection.close()
    execution._store._connection.close()
    evidence = produce_recovery_evidence(
        state_store=state,
        execution=execution,
        risk=risk,
        health_store=None,
        paper_db_path=None,
        receipt_id="unreadable-receipt",
        security_verified=False,
        risk_policy=DEFAULT_POLICY,
    )
    assert evidence.evidence_refs == ()
    assert evidence.independent_replay_passed is False
    assert evidence.gate_results["agent_health"] is False
    assert evidence.gate_results["data_state"] is False
    assert evidence.gate_results["broker_truth"] is False
    failed = _report(evidence).failed_gates
    assert "evidence" in failed
    assert "independent_replay" in failed


# --- (d) endpoint end to end -----------------------------------------------------


def _recovery_headers(key: str) -> dict[str, str]:
    return {
        "X-Operator-Token": OPERATOR,
        "X-Recovery-Token": RECOVERY,
        "Idempotency-Key": key,
    }


def test_endpoint_receipt_carries_produced_evidence(tmp_path):
    _healthy_components(tmp_path)
    app = create_app(
        operator_token=OPERATOR,
        recovery_token=RECOVERY,
        paper_db_path=tmp_path / "paper.sqlite3",
        health_watchdog_enabled=True,
    )
    with TestClient(app) as client:
        # Refusals are unchanged: no recovery token -> 401 before any work.
        missing = client.post(
            RECOVER_URL,
            json={"reason": REASON},
            headers={"X-Operator-Token": OPERATOR, "Idempotency-Key": "no-token"},
        )
        assert missing.status_code == 401

        response = client.post(
            RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("recover")
        )
        assert response.status_code == 200
        receipt = response.json()
        assert receipt["status"] == "COMPLETED"
        assert receipt["safety_state"] == "NORMAL"
        shadow = json.loads(receipt["recovery_verifier_shadow"])
        assert shadow["mode"] == "SHADOW"
        assert shadow["verdict"] == "REJECTED"
        # The replay genuinely ran against the on-disk DB, triage and
        # health evidence were produced, so only the unproducible canary
        # gate remains failed.
        assert shadow["failed_gates"] == ["canary"]
        assert shadow["evidence_refs"]
        assert any(ref.startswith("independent-replay:sha256:") for ref in shadow["evidence_refs"])
        assert any(
            ref.startswith("paper-state-snapshot:sha256:") for ref in shadow["evidence_refs"]
        )

        # Idempotent replay returns the identical receipt, shadow included.
        replay = client.post(
            RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("recover")
        )
        assert replay.status_code == 200
        assert replay.json() == receipt

        # Recovery from NORMAL with a fresh key is still refused.
        refused = client.post(
            RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("again")
        )
        assert refused.status_code == 409
        assert client.get("/api/v1/risk").json()["safety_state"] == "NORMAL"

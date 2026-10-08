"""Authenticated, well-formed rejected controls have durable terminal receipts."""

from datetime import timedelta
import sqlite3
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app
from test_operator_controls import command, send
from test_operator_sessions import NOW, OPERATOR, login


def test_published_rejection_schema_matches_runtime():
    from ai_quant_os.operator_rejection_contracts import OperatorControlRejection

    path = (
        Path(__file__).resolve().parents[1] / "docs/schemas/operator-control-rejection.schema.json"
    )
    assert path.is_file(), "Published rejection receipt required"
    assert json.loads(path.read_bytes()) == OperatorControlRejection.model_json_schema()


def test_rejection_transaction_rechecks_authoritative_state(tmp_path):
    from ai_quant_os.paper_state_store import PaperStateStore
    from ai_quant_os.risk import SafetyState

    store = PaperStateStore(tmp_path / "paper.sqlite3")
    store.record_control("halt", SafetyState.HALT, "Containment", {"receipt_id": "halt"})
    count = store.audit_integrity().event_count
    with pytest.raises(ValueError, match="Safety state changed"):
        store.record_operator_rejection(
            "operator:stale",
            "Stale",
            {
                "decision": "REJECTED",
                "previous_state": "NORMAL",
                "authoritative_state": "NORMAL",
                "receipt_id": "stale",
            },
        )
    assert store.audit_integrity().event_count == count
    assert store.control_receipt("operator:stale") is None
    assert store.safety_state() == SafetyState.HALT


def test_original_rejection_never_becomes_executable_when_state_catches_up():
    client = TestClient(create_app(operator_token=OPERATOR, clock=lambda: NOW))
    session = login(client)
    payload = command(session, action="HALT", expected="NO_NEW_RISK")
    rejected = send(client, session, payload).json()
    assert rejected["decision"] == "REJECTED"
    assert (
        client.post(
            "/api/v1/control/no-new-risk",
            headers={
                "X-Operator-Token": OPERATOR,
                "Idempotency-Key": "state-caught-up",
            },
            json={"reason": "Separate operator containment"},
        ).status_code
        == 200
    )
    assert send(client, session, payload).json() == rejected
    assert client.get("/api/v1/system/status").json()["safety_state"] == "NO_NEW_RISK"


@pytest.mark.parametrize("cause", ["STALE_STATE", "STALE_COMMAND", "SAFETY_RECOVERY_FORBIDDEN"])
def test_rejection_survives_restart_and_does_not_change_safety(tmp_path, cause):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(create_app(operator_token=OPERATOR, paper_db_path=path, clock=lambda: NOW))
    session = login(client)
    expected = "NORMAL"
    if cause == "SAFETY_RECOVERY_FORBIDDEN":
        assert (
            client.post(
                "/api/v1/control/halt",
                headers={
                    "X-Operator-Token": OPERATOR,
                    "Idempotency-Key": "prior-halt",
                },
                json={"reason": "Paper containment"},
            ).status_code
            == 200
        )
        expected = "HALT"
    payload = command(session, expected=expected)
    if cause == "STALE_STATE":
        payload["expected_current_state"] = "HALT"
    elif cause == "STALE_COMMAND":
        payload["client_timestamp"] = (NOW - timedelta(minutes=2)).isoformat()
    response = send(client, session, payload)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["decision"] == "REJECTED" and receipt["rejection_reason"] == cause
    assert receipt["previous_state"] == receipt["authoritative_state"] == expected
    assert receipt["action_id"] == payload["action_id"]
    restarted = TestClient(
        create_app(operator_token=OPERATOR, paper_db_path=path, clock=lambda: NOW)
    )
    assert send(restarted, session, payload).json() == receipt
    assert (
        restarted.get(
            "/api/v1/operator/controls/" + payload["action_id"],
            headers={
                "X-Operator-Session": session["session_token"],
            },
        ).json()
        == receipt
    )
    assert send(restarted, session, {**payload, "reason": "Changed request"}).status_code == 409
    assert restarted.get("/api/v1/system/status").json()["safety_state"] == expected
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE control_receipts SET receipt_json='{}' WHERE idempotency_key=?",
            ("operator:" + payload["action_id"],),
        )
    assert send(restarted, session, payload).status_code == 503

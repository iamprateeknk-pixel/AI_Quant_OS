"""Session-bound safety commands compare state and retain their exact receipts."""

from uuid import uuid4
import sqlite3
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app
from test_operator_sessions import NOW, OPERATOR, login


@pytest.mark.parametrize(
    "name,model_name",
    [
        ("operator-control", "OperatorControl"),
        ("operator-control-receipt", "OperatorControlReceipt"),
    ],
)
def test_published_operator_control_contract_matches_runtime(name, model_name):
    from ai_quant_os import operator_control_api

    path = Path(__file__).resolve().parents[1] / "docs/schemas" / (name + ".schema.json")
    assert path.is_file(), "Published operator command/receipt contract required"
    assert (
        json.loads(path.read_bytes())
        == getattr(operator_control_api, model_name).model_json_schema()
    )


def command(session, action="NO_NEW_RISK", expected="NORMAL"):
    return {
        "action_id": str(uuid4()),
        "requested_action": action,
        "reason": "Paper operator safety decision",
        "operator_session_id": session["session_id"],
        "client_timestamp": NOW.isoformat(),
        "expected_current_state": expected,
        "confirmation": action if action in ("HALT", "FLATTEN_ONLY") else "",
    }


def send(client, session, payload):
    return client.post(
        "/api/v1/operator/controls",
        json=payload,
        headers={
            "X-Operator-Session": session["session_token"],
            "X-Operator-Reauth": OPERATOR,
        },
    )


def test_control_receipt_is_durable_and_retry_preserves_original_decision(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(create_app(operator_token=OPERATOR, paper_db_path=path, clock=lambda: NOW))
    session = login(client)
    payload = command(session)
    response = send(client, session, payload)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["decision"] == "ACCEPTED"
    assert receipt["previous_state"] == "NORMAL"
    assert receipt["authoritative_state"] == "NO_NEW_RISK"
    assert receipt["trace_id"] == receipt["receipt_id"]
    assert receipt["policy_checks"] and receipt["server_timestamp"]
    restarted = TestClient(
        create_app(operator_token=OPERATOR, paper_db_path=path, clock=lambda: NOW)
    )
    assert send(restarted, session, payload).json() == receipt
    assert send(restarted, session, {**payload, "reason": "Changed"}).status_code == 409
    assert (
        restarted.get(
            "/api/v1/operator/controls/" + payload["action_id"],
            headers={
                "X-Operator-Session": session["session_token"],
            },
        ).json()
        == receipt
    )


@pytest.mark.parametrize(
    "change",
    [
        {"expected_current_state": "HALT"},
        {"operator_session_id": str(uuid4())},
        {"requested_action": "HALT", "confirmation": ""},
        {"requested_action": "FLATTEN_ONLY", "confirmation": "HALT"},
        {"requested_action": "NORMAL"},
        {"client_timestamp": "2020-01-01T00:00:00Z"},
    ],
)
def test_unadmitted_control_never_changes_state(change):
    client = TestClient(create_app(operator_token=OPERATOR, clock=lambda: NOW))
    session = login(client)
    response = send(client, session, {**command(session), **change})
    if response.status_code == 200:
        assert response.json()["decision"] == "REJECTED"
        assert (
            response.json()["previous_state"] == response.json()["authoritative_state"] == "NORMAL"
        )
    else:
        assert response.status_code in (403, 409, 422), response.text
    assert client.get("/api/v1/system/status").json()["safety_state"] == "NORMAL"


def test_changed_durable_receipt_cannot_be_replayed(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(create_app(operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=path))
    session = login(client)
    payload = command(session)
    assert send(client, session, payload).status_code == 200
    with sqlite3.connect(path) as db:
        db.execute("UPDATE control_receipts SET receipt_json='{}'")
    assert send(client, session, payload).status_code == 503


def test_expected_state_is_checked_inside_storage_transaction(tmp_path):
    from ai_quant_os.paper_state_store import PaperStateStore
    from ai_quant_os.risk import SafetyState

    store = PaperStateStore(tmp_path / "paper.sqlite3")
    store.record_control("first", SafetyState.HALT, "Emergency", {"receipt_id": "first"})
    with pytest.raises(ValueError, match="Expected safety state"):
        store.record_control(
            "second",
            SafetyState.NO_NEW_RISK,
            "Stale",
            {
                "receipt_id": "second",
            },
            expected_state=SafetyState.NORMAL,
        )
    assert store.control_receipt("second") is None
    assert store.safety_state() == SafetyState.HALT


def test_legacy_idempotency_keys_keep_their_original_contract():
    client = TestClient(create_app(operator_token=OPERATOR, clock=lambda: NOW))
    headers = {"X-Operator-Token": OPERATOR, "Idempotency-Key": "operator:legacy-key"}
    payload = {"reason": "Legacy emergency halt"}
    first = client.post("/api/v1/control/halt", headers=headers, json=payload)
    assert first.status_code == 200
    assert client.post("/api/v1/control/halt", headers=headers, json=payload).json() == first.json()


def test_session_must_still_be_current_at_command_commit():
    from datetime import timedelta

    ticks = []
    client = TestClient(
        create_app(operator_token=OPERATOR, clock=lambda: ticks.pop(0) if ticks else NOW)
    )
    session = login(client)
    payload = command(session)
    expiry = NOW + timedelta(minutes=15)
    payload["client_timestamp"] = (expiry + timedelta(seconds=1)).isoformat()
    # Both HTTP dependencies admit immediately before expiry; the control clock
    # advances while waiting for the mutation lock.
    ticks.extend([expiry - timedelta(seconds=1)] * 2 + [expiry + timedelta(seconds=1)])
    assert send(client, session, payload).status_code == 401
    assert client.get("/api/v1/system/status").json()["safety_state"] == "NORMAL"

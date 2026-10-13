"""Broker route admission controls are backend-authoritative and session-bound."""

from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_operator_sessions import NOW, OPERATOR, login


def command(session, *, requested_enabled, expected_enabled, at=NOW):
    return {
        "action_id": str(uuid4()),
        "requested_enabled": requested_enabled,
        "expected_enabled": expected_enabled,
        "reason": "Scheduled broker route maintenance",
        "operator_session_id": session["session_id"],
        "client_timestamp": at.isoformat(),
        "confirmation": "ENABLE" if requested_enabled else "DISABLE",
    }


def send(client, session, payload, *, reauth=OPERATOR):
    headers = {"X-Operator-Session": session["session_token"]}
    if reauth is not None:
        headers["X-Operator-Reauth"] = reauth
    return client.post("/api/v1/brokers/PAPER/route-controls", json=payload, headers=headers)


def test_route_control_requires_session_and_fresh_reauthentication(tmp_path):
    client = TestClient(
        create_app(operator_token=OPERATOR, paper_db_path=tmp_path / "paper.sqlite3", clock=lambda: NOW)
    )
    session = login(client)
    payload = command(session, requested_enabled=False, expected_enabled=True)

    assert send(client, session, payload, reauth=None).status_code == 401
    assert client.post(
        "/api/v1/brokers/PAPER/route-controls",
        json=payload,
        headers={"X-Operator-Reauth": OPERATOR},
    ).status_code == 401


def test_route_disable_receipt_is_durable_idempotent_and_does_not_change_safety(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(create_app(operator_token=OPERATOR, paper_db_path=path, clock=lambda: NOW))
    session = login(client)
    payload = command(session, requested_enabled=False, expected_enabled=True)

    response = send(client, session, payload)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["decision"] == "ACCEPTED"
    assert receipt["broker_id"] == "PAPER"
    assert receipt["previous_enabled"] is True
    assert receipt["authoritative_enabled"] is False
    assert receipt["policy_checks"] == [
        "PAPER_ONLY",
        "VALID_SESSION",
        "FRESH_REAUTH",
        "EXPECTED_STATE",
        "NEW_ORDERS_ONLY",
    ]
    assert client.get("/api/v1/brokers").json()["brokers"][0]["enabled"] is False
    assert client.get("/api/v1/system/status").json()["safety_state"] == "NORMAL"
    assert send(client, session, payload).json() == receipt

    restarted = TestClient(
        create_app(operator_token=OPERATOR, paper_db_path=path, clock=lambda: NOW)
    )
    assert restarted.get("/api/v1/brokers").json()["brokers"][0]["enabled"] is False
    assert send(restarted, session, payload).json() == receipt
    assert (
        restarted.get(
            "/api/v1/brokers/route-controls/" + payload["action_id"],
            headers={"X-Operator-Session": session["session_token"]},
        ).json()
        == receipt
    )


def test_stale_route_command_is_recorded_without_enabling_route(tmp_path):
    client = TestClient(
        create_app(operator_token=OPERATOR, paper_db_path=tmp_path / "paper.sqlite3", clock=lambda: NOW)
    )
    session = login(client)
    payload = command(
        session,
        requested_enabled=False,
        expected_enabled=True,
        at=NOW - timedelta(minutes=2),
    )

    response = send(client, session, payload)

    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "REJECTED"
    assert response.json()["rejection_reason"] == "STALE_COMMAND"
    assert response.json()["authoritative_enabled"] is True
    assert client.get("/api/v1/brokers").json()["brokers"][0]["enabled"] is True

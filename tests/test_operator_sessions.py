"""Backend-owned sessions expire, revoke and never persist raw credentials."""

from datetime import datetime, timedelta, timezone
import importlib
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
OPERATOR = "synthetic-operator-credential-for-session-tests"


def store_type():
    assert importlib.util.find_spec("ai_quant_os.operator_session_store"), (
        "Durable backend-owned operator sessions required"
    )
    return importlib.import_module("ai_quant_os.operator_session_store").OperatorSessionStore


def test_issued_session_survives_restart_without_raw_credentials(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    with store_type()(path) as store:
        token, session = store.issue(NOW, "a" * 64)
        assert store.authenticate(token, NOW, "a" * 64) == session
        assert session.expires_at == NOW + timedelta(minutes=15)
    assert token.encode() not in path.read_bytes()
    with store_type()(path) as store:
        assert store.authenticate(token, NOW + timedelta(minutes=1), "a" * 64) == session
        assert store.authenticate(token, session.expires_at, "a" * 64) is None
        assert store.authenticate(token, NOW - timedelta(seconds=1), "a" * 64) is None
        assert store.authenticate(token, NOW, "b" * 64) is None
        assert store.authenticate("wrong", NOW, "a" * 64) is None
        store.revoke(token, NOW + timedelta(seconds=1), "a" * 64)
        assert store.authenticate(token, NOW + timedelta(seconds=2), "a" * 64) is None
    with store_type()(path) as store:
        assert store.authenticate(token, NOW + timedelta(seconds=2), "a" * 64) is None


def test_session_capacity_can_only_be_reclaimed_from_expired_or_revoked_grants(tmp_path):
    with store_type()(tmp_path / "sessions.sqlite3", capacity=2) as store:
        first, session = store.issue(NOW, "a" * 64)
        store.issue(NOW, "a" * 64)
        with pytest.raises(ValueError, match="capacity"):
            store.issue(NOW, "a" * 64)
        store.revoke(first, NOW + timedelta(seconds=1), "a" * 64)
        store.issue(NOW + timedelta(seconds=1), "a" * 64)
        fresh, _ = store.issue(session.expires_at + timedelta(seconds=2), "a" * 64)
        assert store.authenticate(fresh, session.expires_at + timedelta(seconds=2), "a" * 64)


@pytest.mark.parametrize("change", ["projection", "event", "head"])
def test_changed_session_journal_withholds_authorization(tmp_path, change):
    path = tmp_path / "sessions.sqlite3"
    with store_type()(path) as store:
        token, _ = store.issue(NOW, "a" * 64)
    with sqlite3.connect(path) as db:
        if change == "projection":
            db.execute("UPDATE operator_sessions SET session_json='{}'")
        elif change == "event":
            db.execute("UPDATE audit_events SET event_json='{}'")
        else:
            db.execute("UPDATE audit_head SET head_hash=?", ("0" * 64,))
    with pytest.raises(ValueError):
        with store_type()(path) as store:
            store.authenticate(token, NOW, "a" * 64)


def login(client):
    response = client.post("/api/v1/operator/sessions", headers={"X-Operator-Token": OPERATOR})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["mode"] == "PAPER" and result["expires_at"]
    assert OPERATOR not in response.text
    return result


def test_session_login_read_expiry_and_logout_are_backend_authoritative(tmp_path):
    now = [NOW]
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token=OPERATOR, clock=lambda: now[0], paper_db_path=path)
    )
    assert client.post("/api/v1/operator/sessions").status_code == 401
    session = login(client)
    headers = {"X-Operator-Session": session["session_token"]}
    response = client.get("/api/v1/operator/session", headers=headers)
    assert response.status_code == 200 and response.json()["session_id"] == session["session_id"]
    assert session["session_token"] not in response.text
    assert client.get("/api/v1/paper/evidence/scheduler", headers=headers).status_code == 200
    restarted = TestClient(
        create_app(operator_token=OPERATOR, clock=lambda: now[0], paper_db_path=path)
    )
    assert restarted.get("/api/v1/operator/session", headers=headers).status_code == 200
    assert restarted.delete("/api/v1/operator/session", headers=headers).status_code == 200
    assert client.get("/api/v1/operator/session", headers=headers).status_code == 401
    session = login(restarted)
    now[0] += timedelta(minutes=15)
    assert (
        restarted.get(
            "/api/v1/operator/session",
            headers={
                "X-Operator-Session": session["session_token"],
            },
        ).status_code
        == 401
    )


@pytest.mark.parametrize("action", ["no-new-risk", "flatten-only", "halt"])
def test_session_safety_commands_require_fresh_operator_credential(action):
    client = TestClient(create_app(operator_token=OPERATOR, clock=lambda: NOW))
    session = login(client)
    headers = {"X-Operator-Session": session["session_token"], "Idempotency-Key": "session-action"}
    path = "/api/v1/control/" + action
    payload = {"reason": "Operator requested paper safety transition"}
    assert client.post(path, headers=headers, json=payload).status_code == 401
    assert (
        client.post(
            path, headers={**headers, "X-Operator-Reauth": "wrong"}, json=payload
        ).status_code
        == 401
    )
    response = client.post(path, headers={**headers, "X-Operator-Reauth": OPERATOR}, json=payload)
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    assert response.json()["safety_state"] == action.replace("-", "_").upper()
    repeated = client.post(path, headers={**headers, "X-Operator-Reauth": OPERATOR}, json=payload)
    assert repeated.json() == response.json()


def test_credential_rotation_revokes_existing_session_but_direct_emergency_access_survives(
    tmp_path,
):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(create_app(operator_token=OPERATOR, paper_db_path=path, clock=lambda: NOW))
    session = login(client)
    rotated = TestClient(
        create_app(operator_token=OPERATOR + "-rotated", paper_db_path=path, clock=lambda: NOW)
    )
    assert (
        rotated.get(
            "/api/v1/operator/session",
            headers={
                "X-Operator-Session": session["session_token"],
            },
        ).status_code
        == 401
    )
    response = rotated.post(
        "/api/v1/control/halt",
        headers={
            "X-Operator-Token": OPERATOR + "-rotated",
            "Idempotency-Key": "emergency-halt",
        },
        json={"reason": "Operator emergency stop"},
    )
    assert response.status_code == 200


def test_corrupt_session_journal_withholds_sessions_without_disabling_emergency_halt(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(create_app(operator_token=OPERATOR, paper_db_path=path, clock=lambda: NOW))
    session = login(client)
    with sqlite3.connect(tmp_path / "paper.operator-sessions.sqlite3") as db:
        db.execute("UPDATE operator_sessions SET session_json='{}'")
    restarted = TestClient(
        create_app(operator_token=OPERATOR, paper_db_path=path, clock=lambda: NOW)
    )
    assert (
        restarted.get(
            "/api/v1/operator/session",
            headers={
                "X-Operator-Session": session["session_token"],
            },
        ).status_code
        == 503
    )
    assert (
        restarted.post(
            "/api/v1/operator/sessions", headers={"X-Operator-Token": OPERATOR}
        ).status_code
        == 503
    )
    assert (
        restarted.post(
            "/api/v1/control/halt",
            headers={
                "X-Operator-Token": OPERATOR,
                "Idempotency-Key": "corrupt-session-emergency",
            },
            json={"reason": "Emergency paper halt"},
        ).status_code
        == 200
    )

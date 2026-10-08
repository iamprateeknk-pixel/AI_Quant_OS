"""An admitted original command has a durable reservation before safety mutation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import sqlite3
import pytest
import json
from pathlib import Path

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.risk import RiskKernel
from test_operator_controls import command, send
from test_operator_sessions import NOW, OPERATOR, login


def test_published_pending_schema_matches_runtime():
    from ai_quant_os.operator_pending_contracts import OperatorControlPending

    path = Path(__file__).resolve().parents[1] / "docs/schemas/operator-control-pending.schema.json"
    assert path.is_file(), "Published pending contract required"
    assert json.loads(path.read_bytes()) == OperatorControlPending.model_json_schema()


def test_pending_receipt_is_visible_while_original_command_waits(tmp_path, monkeypatch):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(create_app(operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=path))
    session = login(client)
    payload = command(session)
    entered, release = Event(), Event()
    original = RiskKernel.set_safety_state

    def wait_before_mutation(self, *args, **kwargs):
        if kwargs.get("before_change") is not None:
            entered.set()
            assert release.wait(5), "Test-owned command was not released"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RiskKernel, "set_safety_state", wait_before_mutation)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(send, client, session, payload)
        try:
            assert entered.wait(3)
            response = client.get(
                "/api/v1/operator/controls/" + payload["action_id"],
                headers={
                    "X-Operator-Session": session["session_token"],
                },
            )
            assert response.status_code == 200, response.text
            pending = response.json()
            assert pending["decision"] == "PENDING"
            assert pending["previous_state"] == pending["authoritative_state"] == "NORMAL"
            assert client.get("/api/v1/system/status").json()["safety_state"] == "NORMAL"
            restarted = TestClient(
                create_app(operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=path)
            )
            assert (
                restarted.get(
                    "/api/v1/operator/controls/" + payload["action_id"],
                    headers={
                        "X-Operator-Session": session["session_token"],
                    },
                ).json()
                == pending
            )
        finally:
            release.set()
        accepted = future.result().json()
    assert accepted["decision"] == "ACCEPTED"
    assert accepted["receipt_id"] == pending["receipt_id"]
    assert (
        client.get(
            "/api/v1/operator/controls/" + payload["action_id"],
            headers={
                "X-Operator-Session": session["session_token"],
            },
        ).json()
        == accepted
    )


@pytest.mark.parametrize("change", [None, "ALTERED", "MISSING"])
def test_interrupted_reservation_requires_explicit_original_retry(tmp_path, monkeypatch, change):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(create_app(operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=path))
    session = login(client)
    payload = command(session)
    original = RiskKernel.set_safety_state

    def interrupt(self, *args, **kwargs):
        if kwargs.get("before_change") is not None:
            raise RuntimeError("Synthetic interruption before mutation")
        return original(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(RiskKernel, "set_safety_state", interrupt)
        with pytest.raises(RuntimeError, match="Synthetic interruption"):
            send(client, session, payload)
    if change:
        with sqlite3.connect(path) as db:
            db.execute(
                "UPDATE operator_reservations SET pending_json='{}'"
                if change == "ALTERED"
                else "DELETE FROM operator_reservations"
            )
    restarted = TestClient(
        create_app(operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=path)
    )
    read = restarted.get(
        "/api/v1/operator/controls/" + payload["action_id"],
        headers={
            "X-Operator-Session": session["session_token"],
        },
    )
    assert restarted.get("/api/v1/system/status").json()["safety_state"] == "NORMAL"
    if change:
        assert read.status_code == 503
        assert send(restarted, session, payload).status_code == 503
        return
    pending = read.json()
    assert pending["decision"] == "PENDING"
    assert send(restarted, session, {**payload, "reason": "Changed request"}).status_code == 409
    accepted = send(restarted, session, payload).json()
    assert accepted["decision"] == "ACCEPTED" and accepted["receipt_id"] == pending["receipt_id"]

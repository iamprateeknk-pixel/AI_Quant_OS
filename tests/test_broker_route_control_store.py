import sqlite3

import pytest

from ai_quant_os.paper_state_store import OperatorReservationConflict, PaperStateStore


def command(action_id: str, *, expected_enabled: bool, requested_enabled: bool, fingerprint: str):
    return {
        "schema_version": "1.0",
        "mode": "PAPER",
        "broker_id": "PAPER",
        "action_id": action_id,
        "decision": "PENDING",
        "expected_enabled": expected_enabled,
        "requested_enabled": requested_enabled,
        "operator_session_id": "90bb3203-550b-4cf1-bcca-83f764e1d009",
        "request_sha256": fingerprint,
        "server_timestamp": "2026-10-13T00:00:00+00:00",
        "trace_id": action_id,
        "policy_checks": ["PAPER_ONLY", "VALID_SESSION", "FRESH_REAUTH"],
    }


def test_broker_route_control_is_durable_idempotent_and_audited(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    assert store.broker_route_enabled("PAPER", default_enabled=True) is True
    receipt = command(
        "10bb3203-550b-4cf1-bcca-83f764e1d009",
        expected_enabled=True,
        requested_enabled=False,
        fingerprint="a" * 64,
    )

    accepted = store.record_broker_route_control(
        "broker-route:" + receipt["action_id"], "maintenance", receipt, current_enabled=True
    )

    assert accepted["decision"] == "ACCEPTED"
    assert accepted["previous_enabled"] is True
    assert accepted["authoritative_enabled"] is False
    assert store.broker_route_enabled("PAPER", default_enabled=True) is False
    assert store.record_broker_route_control(
        "broker-route:" + receipt["action_id"], "maintenance", receipt, current_enabled=False
    ) == accepted
    reopened = PaperStateStore(path)
    assert reopened.broker_route_enabled("PAPER", default_enabled=True) is False
    assert reopened.broker_route_control_receipt(receipt["action_id"]) == accepted
    assert reopened.audit_integrity().valid is True


def test_route_state_tampering_and_action_identity_reuse_are_rejected(tmp_path):
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    store.broker_route_enabled("PAPER", default_enabled=True)
    receipt = command(
        "10bb3203-550b-4cf1-bcca-83f764e1d009",
        expected_enabled=True,
        requested_enabled=False,
        fingerprint="a" * 64,
    )
    store.record_broker_route_control(
        "broker-route:" + receipt["action_id"], "maintenance", receipt, current_enabled=True
    )
    reused = {**receipt, "request_sha256": "b" * 64}
    with pytest.raises(OperatorReservationConflict):
        store.record_broker_route_control(
            "broker-route:" + receipt["action_id"], "different request", reused, current_enabled=False
        )

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE broker_route_state SET enabled=1 WHERE broker_id='PAPER'")
    with pytest.raises(ValueError, match="route state.*verified|receipt.*binding"):
        store.broker_route_enabled("PAPER", default_enabled=True)


def test_stale_command_receipt_is_audited_without_changing_route_state(tmp_path):
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    assert store.broker_route_enabled("PAPER", default_enabled=True) is True
    receipt = command(
        "20bb3203-550b-4cf1-bcca-83f764e1d009",
        expected_enabled=True,
        requested_enabled=False,
        fingerprint="c" * 64,
    )
    rejected = store.record_broker_route_control(
        "broker-route:" + receipt["action_id"],
        "maintenance",
        receipt,
        current_enabled=True,
        rejection_reason="STALE_COMMAND",
    )
    assert rejected["decision"] == "REJECTED"
    assert rejected["rejection_reason"] == "STALE_COMMAND"
    assert rejected["authoritative_enabled"] is True
    assert store.broker_route_enabled("PAPER", default_enabled=True) is True

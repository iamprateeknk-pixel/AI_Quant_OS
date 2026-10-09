from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_operator_sessions import NOW, OPERATOR, login


def test_protected_list_and_reauthenticated_target_revocation_survive_restart(tmp_path):
    args = dict(
        operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
    )
    with TestClient(create_app(**args)) as client:
        actor, target = login(client), login(client)
        headers = {"X-Operator-Session": actor["session_token"]}
        assert client.get("/api/v1/operator/sessions").status_code == 401
        read = client.get("/api/v1/operator/sessions", headers=headers)
        assert read.status_code == 200, read.text
        view = read.json()
        assert view["current_session_id"] == actor["session_id"]
        assert {row["session_id"] for row in view["sessions"]} == {
            actor["session_id"],
            target["session_id"],
        }
        assert actor["session_token"] not in read.text and "token_sha256" not in read.text
        body = {"target_session_id": target["session_id"], "expected_head": view["source_head"]}
        path = "/api/v1/operator/sessions/revoke"
        assert client.post(path, headers=headers, json=body).status_code == 401
        result = client.post(path, headers={**headers, "X-Operator-Reauth": OPERATOR}, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "REVOKED"
        assert result.json()["target_session_id"] == target["session_id"]
        assert (
            client.get(
                "/api/v1/operator/session", headers={"X-Operator-Session": target["session_token"]}
            ).status_code
            == 401
        )
        assert client.get("/api/v1/operator/session", headers=headers).status_code == 200
    with TestClient(create_app(**args)) as client:
        assert (
            client.get(
                "/api/v1/operator/session", headers={"X-Operator-Session": target["session_token"]}
            ).status_code
            == 401
        )
        view = client.get("/api/v1/operator/sessions", headers=headers).json()
        assert len(view["sessions"]) == 1
        assert view["sessions"][0]["session_id"] == actor["session_id"]


def test_stale_head_prevents_cross_session_revocation_and_self_revocation_is_explicit(tmp_path):
    with TestClient(
        create_app(
            operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        actor, target = login(client), login(client)
        headers = {"X-Operator-Session": actor["session_token"], "X-Operator-Reauth": OPERATOR}
        view = client.get("/api/v1/operator/sessions", headers=headers).json()
        login(client)
        body = {"target_session_id": target["session_id"], "expected_head": view["source_head"]}
        assert (
            client.post("/api/v1/operator/sessions/revoke", headers=headers, json=body).status_code
            == 409
        )
        assert (
            client.get(
                "/api/v1/operator/session", headers={"X-Operator-Session": target["session_token"]}
            ).status_code
            == 200
        )
        view = client.get("/api/v1/operator/sessions", headers=headers).json()
        result = client.post(
            "/api/v1/operator/sessions/revoke",
            headers=headers,
            json={"target_session_id": actor["session_id"], "expected_head": view["source_head"]},
        )
        assert result.status_code == 200 and result.json()["current_session_revoked"] is True
        assert client.get("/api/v1/operator/sessions", headers=headers).status_code == 401


def test_concurrent_revocation_uses_single_original_inventory_head(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with TestClient(
        create_app(
            operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        actor, first, second = login(client), login(client), login(client)
        headers = {"X-Operator-Session": actor["session_token"], "X-Operator-Reauth": OPERATOR}
        view = client.get("/api/v1/operator/sessions", headers=headers).json()

        def revoke(target):
            return client.post(
                "/api/v1/operator/sessions/revoke",
                headers=headers,
                json={
                    "target_session_id": target["session_id"],
                    "expected_head": view["source_head"],
                },
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(revoke, (first, second))) == [200, 409]
        assert len(client.get("/api/v1/operator/sessions", headers=headers).json()["sessions"]) == 2


def test_expired_session_cannot_administer_and_rotated_credentials_hide_old_grants(tmp_path):
    moment = [NOW]
    args = dict(
        operator_token=OPERATOR, clock=lambda: moment[0], paper_db_path=tmp_path / "paper.sqlite3"
    )
    with TestClient(create_app(**args)) as client:
        old = login(client)
        moment[0] += timedelta(minutes=14)
        actor = login(client)
        moment[0] += timedelta(minutes=1)
        headers = {"X-Operator-Session": actor["session_token"]}
        view = client.get("/api/v1/operator/sessions", headers=headers).json()
        assert (
            next(row for row in view["sessions"] if row["session_id"] == old["session_id"])[
                "status"
            ]
            == "EXPIRED"
        )
        assert (
            client.get(
                "/api/v1/operator/sessions", headers={"X-Operator-Session": old["session_token"]}
            ).status_code
            == 401
        )
    with TestClient(create_app(**{**args, "operator_token": "rotated-fixture"})) as client:
        fresh = client.post(
            "/api/v1/operator/sessions", headers={"X-Operator-Token": "rotated-fixture"}
        ).json()
        view = client.get(
            "/api/v1/operator/sessions", headers={"X-Operator-Session": fresh["session_token"]}
        ).json()
        assert [row["session_id"] for row in view["sessions"]] == [fresh["session_id"]]
        result = client.post(
            "/api/v1/operator/sessions/revoke",
            headers={
                "X-Operator-Session": fresh["session_token"],
                "X-Operator-Reauth": "rotated-fixture",
            },
            json={"target_session_id": actor["session_id"], "expected_head": view["source_head"]},
        )
        assert result.status_code == 404


def test_corrupt_admin_revocation_binding_refuses_history_and_preserves_emergency_token_controls(
    tmp_path,
):
    import sqlite3
    import json
    from ai_quant_os.audit_chain import event_hash

    path = tmp_path / "paper.sqlite3"
    args = dict(operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=path)
    with TestClient(create_app(**args)) as client:
        actor, target = login(client), login(client)
        headers = {"X-Operator-Session": actor["session_token"], "X-Operator-Reauth": OPERATOR}
        view = client.get("/api/v1/operator/sessions", headers=headers).json()
        assert (
            client.post(
                "/api/v1/operator/sessions/revoke",
                headers=headers,
                json={
                    "target_session_id": target["session_id"],
                    "expected_head": view["source_head"],
                },
            ).status_code
            == 200
        )
        with sqlite3.connect(tmp_path / "paper.operator-sessions.sqlite3") as db:
            key = "admin-revoke:" + target["session_id"]
            row = db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (key,)
            ).fetchone()
            event = json.loads(row[0])
            event["expected_head"] = "0" * 64
            raw = json.dumps(event, sort_keys=True, separators=(",", ":"))
            sequence, previous = db.execute(
                "SELECT sequence,previous_hash FROM audit_chain WHERE event_key=?", (key,)
            ).fetchone()
            # Forge a coherent outer chain; original request-head binding must still reject it.
            digest = event_hash(sequence, key, raw, previous)
            db.execute("UPDATE audit_events SET event_json=? WHERE event_key=?", (raw, key))
            db.execute("UPDATE audit_chain SET event_hash=? WHERE event_key=?", (digest, key))
            db.execute("UPDATE audit_head SET head_hash=?", (digest,))
        assert client.get("/api/v1/operator/sessions", headers=headers).status_code == 503
        response = client.post(
            "/api/v1/control/no-new-risk",
            headers={"X-Operator-Token": OPERATOR, "Idempotency-Key": "session-emergency"},
            json={"reason": "session archive fixture"},
        )
        assert response.status_code == 200


def test_actor_expiry_after_http_admission_prevents_transactional_revocation(tmp_path, monkeypatch):
    from ai_quant_os.operator_session_store import OperatorSessionStore

    moment = [NOW]
    original = OperatorSessionStore.administrative_revoke

    def delayed(store, token, target, expected_head, now, epoch):
        moment[0] += timedelta(minutes=15)
        return original(store, token, target, expected_head, moment[0], epoch)

    with TestClient(
        create_app(
            operator_token=OPERATOR,
            clock=lambda: moment[0],
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        actor, target = login(client), login(client)
        headers = {"X-Operator-Session": actor["session_token"], "X-Operator-Reauth": OPERATOR}
        view = client.get("/api/v1/operator/sessions", headers=headers).json()
        monkeypatch.setattr(OperatorSessionStore, "administrative_revoke", delayed)
        result = client.post(
            "/api/v1/operator/sessions/revoke",
            headers=headers,
            json={"target_session_id": target["session_id"], "expected_head": view["source_head"]},
        )
        assert result.status_code == 401
        moment[0] = NOW
        assert (
            client.get("/api/v1/operator/sessions", headers=headers).json()["source_head"]
            == view["source_head"]
        )


def test_projection_write_failure_rolls_back_administrative_event(tmp_path):
    import sqlite3

    with TestClient(
        create_app(
            operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        actor, target = login(client), login(client)
        headers = {"X-Operator-Session": actor["session_token"], "X-Operator-Reauth": OPERATOR}
        view = client.get("/api/v1/operator/sessions", headers=headers).json()
        with sqlite3.connect(tmp_path / "paper.operator-sessions.sqlite3") as db:
            db.execute(
                "CREATE TRIGGER stop_delete BEFORE DELETE ON operator_sessions BEGIN SELECT RAISE(ABORT,'private projection failure'); END"
            )
        result = client.post(
            "/api/v1/operator/sessions/revoke",
            headers=headers,
            json={"target_session_id": target["session_id"], "expected_head": view["source_head"]},
        )
        assert result.status_code == 503 and "private projection failure" not in result.text
        assert client.get("/api/v1/operator/sessions", headers=headers).json() == view
        assert (
            client.get(
                "/api/v1/operator/session", headers={"X-Operator-Session": target["session_token"]}
            ).status_code
            == 200
        )


def test_credential_rotation_can_issue_owner_session_when_prior_grants_fill_capacity(tmp_path):
    from ai_quant_os.operator_session_store import OperatorSessionStore

    with OperatorSessionStore(tmp_path / "sessions.sqlite3", capacity=2) as store:
        old_first, _ = store.issue(NOW, "a" * 64)
        old_second, _ = store.issue(NOW, "a" * 64)
        token, fresh = store.issue(NOW, "b" * 64)
        assert store.authenticate(old_first, NOW, "a" * 64) is None
        assert store.authenticate(old_second, NOW, "a" * 64) is None
        assert store.authenticate(token, NOW, "b" * 64) == fresh
        assert len(store.administrative_view(token, NOW, "b" * 64)["sessions"]) == 1


def test_public_session_admin_contracts_and_schema_exports_match(tmp_path):
    import json
    from pathlib import Path
    from ai_quant_os import operator_session_contracts as contracts

    assert hasattr(contracts, "SessionInventory"), "Typed session inventory missing"
    assert hasattr(contracts, "SessionRevocationReceipt"), "Typed revocation receipt missing"
    root = Path(__file__).resolve().parents[1]
    for model, name in [
        (contracts.SessionInventory, "operator-session-inventory.schema.json"),
        (contracts.SessionRevocationReceipt, "operator-session-revocation.schema.json"),
    ]:
        assert (
            json.loads((root / "docs/schemas" / name).read_text(encoding="utf-8"))
            == model.model_json_schema()
        )
    with TestClient(
        create_app(
            operator_token=OPERATOR, clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        actor, target = login(client), login(client)
        headers = {"X-Operator-Session": actor["session_token"], "X-Operator-Reauth": OPERATOR}
        raw = client.get("/api/v1/operator/sessions", headers=headers).json()
        contracts.SessionInventory.model_validate(raw)
        raw["sessions"][1]["status"] = "EXPIRED"
        with pytest.raises(ValueError):
            contracts.SessionInventory.model_validate(raw)

        receipt = client.post(
            "/api/v1/operator/sessions/revoke",
            headers=headers,
            json={"target_session_id": target["session_id"], "expected_head": raw["source_head"]},
        ).json()
        contracts.SessionRevocationReceipt.model_validate(receipt)
        receipt["current_session_revoked"] = True
        with pytest.raises(ValueError):
            contracts.SessionRevocationReceipt.model_validate(receipt)

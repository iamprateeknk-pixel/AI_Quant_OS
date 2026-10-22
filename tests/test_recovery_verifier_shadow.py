"""Recovery Verifier shadow mode on the operator paper-recovery path.

The shadow verdict is recorded on the recovery receipt for comparison
only. These tests prove the accept/refuse behaviour of
``POST /api/v1/control/recover-paper`` is exactly the pre-shadow
behaviour (200 on a gated recovery from NO_NEW_RISK; 401 without the
recovery token; 503 when recovery is not configured; 409 for HALT,
for recovery from NORMAL, and for idempotency-key reuse with a
different reason) while the receipt additionally carries a well-formed
``recovery_verifier_shadow`` field that survives idempotent replay.

The app runs on the in-memory paper store: accept/refuse semantics and
receipt recording are identical there, and replay equality is checked
against the same store the receipt was recorded in.
"""

import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

OPERATOR = "operator-secret"
RECOVERY = "recovery-secret"
RECOVER_URL = "/api/v1/control/recover-paper"
REASON = "feed restored and paper ledger checked"


def _client(*, recovery_token=RECOVERY):
    return TestClient(create_app(operator_token=OPERATOR, recovery_token=recovery_token))


def _control_headers(key: str) -> dict[str, str]:
    return {"X-Operator-Token": OPERATOR, "Idempotency-Key": key}


def _recovery_headers(key: str) -> dict[str, str]:
    return {**_control_headers(key), "X-Recovery-Token": RECOVERY}


def _stop(client: TestClient, state: str = "no-new-risk", key: str = "stop"):
    response = client.post(
        f"/api/v1/control/{state}",
        json={"reason": "feed interruption"},
        headers=_control_headers(key),
    )
    assert response.status_code == 200


def _shadow(receipt: dict) -> dict:
    raw = receipt["recovery_verifier_shadow"]
    assert isinstance(raw, str)
    return json.loads(raw)


def test_accepted_recovery_carries_well_formed_shadow_verdict() -> None:
    client = _client()
    _stop(client)
    response = client.post(
        RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("recover")
    )
    assert response.status_code == 200
    receipt = response.json()
    # The receipt fields are the pre-existing ones plus the shadow field
    # and the staged-landing canary binding (stage / canary_state /
    # canary_entered_at).
    assert set(receipt) == {
        "receipt_id",
        "status",
        "safety_state",
        "reason",
        "stage",
        "canary_state",
        "canary_entered_at",
        "recovery_verifier_shadow",
    }
    assert receipt["status"] == "COMPLETED"
    # Staged recovery: the accepted recovery lands at REDUCED_RISK as a
    # canary stage; NORMAL now requires the separate promotion step.
    assert receipt["safety_state"] == "REDUCED_RISK"
    assert receipt["stage"] == "CANARY"
    assert receipt["canary_state"] == "REDUCED_RISK"
    assert receipt["reason"] == REASON
    shadow = _shadow(receipt)
    assert shadow["mode"] == "SHADOW"
    # Phase 3: the shadow now carries evidence produced from the live
    # paper system, so gates with real evidence pass (data_state,
    # risk_execution, broker_truth, security, resume_controller). This
    # fixture is an in-memory app with no health watchdog and no triage
    # artifact, and the shadow verdict is computed before the staged
    # landing, so the canary gate cannot pass there either; the verdict
    # is still REJECTED -- naming exactly those genuinely missing
    # gates. It still must not block. (On an on-disk healthy app the
    # replay genuinely runs and independent_replay drops out; see
    # test_recovery_evidence.py.)
    assert shadow["verdict"] == "REJECTED"
    assert shadow["failed_gates"] == [
        "agent_health",
        "repair_verification",
        "canary",
        "independent_replay",
    ]
    assert "independent_replay" in shadow["failed_gates"]
    assert all(isinstance(gate, str) for gate in shadow["failed_gates"])
    # Evidence refs are now the produced hashes, not the bare receipt ref.
    assert shadow["evidence_refs"] != [f"paper-recovery:{receipt['receipt_id']}"]
    assert any(ref.startswith("paper-state-snapshot:sha256:") for ref in shadow["evidence_refs"])
    assert any(ref.startswith("audit-chain:sha256:") for ref in shadow["evidence_refs"])
    assert client.get("/api/v1/risk").json()["safety_state"] == "REDUCED_RISK"


def test_shadow_verdict_survives_idempotent_replay() -> None:
    client = _client()
    _stop(client)
    first = client.post(RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("recover"))
    assert first.status_code == 200
    replay = client.post(RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("recover"))
    assert replay.status_code == 200
    assert replay.json() == first.json()


def test_refusals_match_pre_shadow_behaviour() -> None:
    client = _client()
    # Recovery token missing -> 401, exactly as before the shadow existed.
    _stop(client)
    missing = client.post(RECOVER_URL, json={"reason": REASON}, headers=_control_headers("recover"))
    assert missing.status_code == 401
    # Idempotency key reused with a different reason -> 409.
    accepted = client.post(
        RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("recover")
    )
    assert accepted.status_code == 200
    reused = client.post(
        RECOVER_URL,
        json={"reason": "changed reason"},
        headers=_recovery_headers("recover"),
    )
    assert reused.status_code == 409


def test_recovery_from_halt_is_refused_and_state_unchanged() -> None:
    client = _client()
    _stop(client, state="halt", key="halt")
    response = client.post(
        RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("recover")
    )
    assert response.status_code == 409
    assert client.get("/api/v1/risk").json()["safety_state"] == "HALT"


def test_recovery_from_normal_is_refused() -> None:
    client = _client()
    response = client.post(
        RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("recover")
    )
    assert response.status_code == 409
    assert client.get("/api/v1/risk").json()["safety_state"] == "NORMAL"


def test_recovery_disabled_without_recovery_token() -> None:
    client = _client(recovery_token=None)
    _stop(client)
    response = client.post(
        RECOVER_URL, json={"reason": REASON}, headers=_recovery_headers("recover")
    )
    assert response.status_code == 503

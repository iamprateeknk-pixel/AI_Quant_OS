"""Staged recovery canary (V3.2 section 13): landing, soak, promotion.

Before this increment the Recovery Verifier could never be satisfied on
the paper recovery path: recovery landed directly at NORMAL, so the
``canary`` quorum gate had no honest evidence. These tests pin the
staged semantics that replaced that landing:

* an accepted recovery lands at REDUCED_RISK as a recorded canary stage
  (kernel, execution gateway, state store and endpoint agree);
* ``recovery_evidence`` can produce a real ``canary`` pass -- but only
  against a landed stage whose soak (minimum elapsed time from the
  policy, no health incident detected during the canary) has completed,
  so a healthy soaked system verifies VERIFIED end to end;
* promotion back to NORMAL happens only through the explicit promotion
  step, which re-checks the full quorum and refuses (409, state
  unchanged, no receipt recorded) while any gate fails;
* the legacy direct paths (``recover_paper_safety`` /
  ``record_recovery``) are untouched and keep their contracts.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState
from ai_quant_os.risk_policy import DEFAULT_POLICY, PaperRiskPolicy, load_risk_policy
from test_recovery_evidence import _healthy_components, _produce, _report

OPERATOR = "operator-secret"
RECOVERY = "recovery-secret"
RECOVER_URL = "/api/v1/control/recover-paper"
PROMOTE_URL = "/api/v1/control/recover-paper/promote"
REASON = "feed restored and paper ledger checked"
ENTERED = datetime(2026, 1, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _canary_receipt(entered: datetime, receipt_id: str = "canary-1") -> dict[str, str]:
    return {
        "receipt_id": receipt_id,
        "status": "COMPLETED",
        "safety_state": SafetyState.REDUCED.value,
        "stage": "CANARY",
        "canary_state": SafetyState.REDUCED.value,
        "canary_entered_at": entered.isoformat(),
        "reason": "triaged",
    }


def _kernel_at(state: SafetyState) -> RiskKernel:
    kernel = RiskKernel(RiskLimits(Decimal(1000), Decimal(1500), 5))
    if state is not SafetyState.NORMAL:
        kernel.set_safety_state(state)
    return kernel


# --- kernel -------------------------------------------------------------------


def test_kernel_canary_landing_and_promotion() -> None:
    kernel = _kernel_at(SafetyState.NO_NEW_RISK)
    kernel.stage_paper_recovery_canary(before_change=lambda: None)
    assert kernel.safety_state is SafetyState.REDUCED
    kernel.promote_paper_canary(before_change=lambda: None)
    assert kernel.safety_state is SafetyState.NORMAL


@pytest.mark.parametrize("state", [SafetyState.NORMAL, SafetyState.FLATTEN_ONLY, SafetyState.HALT])
def test_kernel_canary_landing_refused_from_unrecoverable_states(state) -> None:
    kernel = _kernel_at(state)
    with pytest.raises(ValueError):
        kernel.stage_paper_recovery_canary(before_change=lambda: None)
    assert kernel.safety_state is state


def test_kernel_promotion_refused_outside_the_canary() -> None:
    kernel = _kernel_at(SafetyState.NO_NEW_RISK)
    with pytest.raises(ValueError):
        kernel.promote_paper_canary(before_change=lambda: None)
    assert kernel.safety_state is SafetyState.NO_NEW_RISK


# --- state store ----------------------------------------------------------------


def _staged_store(tmp_path, entered: datetime = ENTERED) -> PaperStateStore:
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    store.record_order_uncertainty("order-1")
    store.record_recovery_canary("recover", "triaged", _canary_receipt(entered))
    return store


def test_store_canary_landing_records_the_stage(tmp_path) -> None:
    store = _staged_store(tmp_path)
    assert store.safety_state() is SafetyState.REDUCED
    stage = store.canary_stage()
    assert stage is not None
    assert stage["receipt_id"] == "canary-1"
    assert stage["canary_entered_at"] == ENTERED.isoformat()
    assert stage["canary_state"] == "REDUCED_RISK"


def test_store_canary_landing_requires_the_stage_binding(tmp_path) -> None:
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    store.record_order_uncertainty("order-1")
    with pytest.raises(ValueError):
        store.record_recovery_canary(
            "recover", "triaged", {"receipt_id": "x", "safety_state": "REDUCED_RISK"}
        )
    assert store.safety_state() is SafetyState.NO_NEW_RISK
    assert store.canary_stage() is None


def test_store_promotion_requires_the_matching_canary(tmp_path) -> None:
    store = _staged_store(tmp_path)
    promotion = {"receipt_id": "promo-1", "stage": "PROMOTED", "canary_receipt_id": "other"}
    with pytest.raises(ValueError):
        store.record_canary_promotion("promote", "soaked", promotion)
    assert store.safety_state() is SafetyState.REDUCED
    promotion["canary_receipt_id"] = "canary-1"
    store.record_canary_promotion("promote", "soaked", promotion)
    assert store.safety_state() is SafetyState.NORMAL
    assert store.canary_stage() is None
    with pytest.raises(ValueError):
        store.record_canary_promotion("promote-2", "soaked", {**promotion, "receipt_id": "promo-2"})


def test_bare_reduced_state_is_not_a_canary(tmp_path) -> None:
    # REDUCED_RISK reached without a canary landing receipt (here: the
    # legacy migration path) must never be promotable as a canary.
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    assert store.safety_state() is SafetyState.NORMAL
    assert store.canary_stage() is None


# --- risk policy soak -----------------------------------------------------------


def test_policy_canary_soak_default_loads_and_fails_closed(tmp_path) -> None:
    assert DEFAULT_POLICY.recovery_canary_min_soak_seconds == 300
    payload = json.loads((ROOT / "data" / "risk_policy.paper.v1.json").read_text())
    target = tmp_path / "policy.json"
    target.write_text(json.dumps(payload))
    assert load_risk_policy(target) == DEFAULT_POLICY
    payload["recovery_canary_min_soak_seconds"] = 0
    target.write_text(json.dumps(payload))
    assert load_risk_policy(target).recovery_canary_min_soak_seconds == 0
    for bad in ("300", 300.5, True, -1):
        payload["recovery_canary_min_soak_seconds"] = bad
        target.write_text(json.dumps(payload))
        with pytest.raises(ValueError):
            load_risk_policy(target)
    with pytest.raises(ValueError):
        PaperRiskPolicy(recovery_canary_min_soak_seconds=-1)


# --- evidence production ----------------------------------------------------------


def _stage_components(components, entered: datetime = ENTERED):
    components["execution"].recover_paper_safety_canary(
        before_change=lambda: components["state"].record_recovery_canary(
            "recover", "triaged", _canary_receipt(entered)
        )
    )
    assert components["risk"].safety_state is SafetyState.REDUCED
    assert components["state"].safety_state() is SafetyState.REDUCED
    stage = components["state"].canary_stage()
    assert stage is not None
    return stage


def test_canary_gate_passes_after_soak_and_the_quorum_verifies(tmp_path) -> None:
    components = _healthy_components(tmp_path)
    stage = _stage_components(components)
    early = _produce(
        components,
        canary_stage=stage,
        canary_now=ENTERED + timedelta(seconds=299),
        canary_min_soak_seconds=300,
    )
    assert early.gate_results["canary"] is False
    assert "canary" in _report(early).failed_gates
    soaked = _produce(
        components,
        canary_stage=stage,
        canary_now=ENTERED + timedelta(seconds=300),
        canary_min_soak_seconds=300,
    )
    assert soaked.gate_results["canary"] is True
    assert any(ref.startswith("recovery-canary:sha256:") for ref in soaked.evidence_refs)
    report = _report(soaked)
    assert report.verdict == "VERIFIED"
    assert report.failed_gates == ()


def test_canary_gate_fails_on_an_incident_during_the_canary(tmp_path) -> None:
    components = _healthy_components(tmp_path)
    stage = _stage_components(components)
    components["health_store"].record(
        HealthIncident(
            "QUOTE_STALE",
            "EURUSD",
            '{"source":"canary-test"}',
            ENTERED + timedelta(seconds=60),
        )
    )
    evidence = _produce(
        components,
        canary_stage=stage,
        canary_now=ENTERED + timedelta(seconds=600),
        canary_min_soak_seconds=300,
    )
    assert evidence.gate_results["canary"] is False
    assert "canary" in _report(evidence).failed_gates


def test_canary_gate_ignores_incidents_from_before_entry(tmp_path) -> None:
    components = _healthy_components(tmp_path)
    stage = _stage_components(components)
    components["health_store"].record(
        HealthIncident(
            "QUOTE_STALE",
            "EURUSD",
            '{"source":"canary-test"}',
            ENTERED - timedelta(seconds=60),
        )
    )
    evidence = _produce(
        components,
        canary_stage=stage,
        canary_now=ENTERED + timedelta(seconds=600),
        canary_min_soak_seconds=300,
    )
    # The pre-entry incident is not a soak breach. (It is still open, so
    # agent_health fails on its own -- the canary verdict is separate.)
    assert evidence.gate_results["canary"] is True


def test_canary_gate_requires_the_staged_state(tmp_path) -> None:
    components = _healthy_components(tmp_path)
    ghost = {
        "receipt_id": "ghost",
        "idempotency_key": "recover",
        "canary_entered_at": ENTERED.isoformat(),
        "canary_state": "REDUCED_RISK",
        "reason": "triaged",
    }
    evidence = _produce(
        components,
        canary_stage=ghost,
        canary_now=ENTERED + timedelta(hours=1),
        canary_min_soak_seconds=0,
    )
    assert evidence.gate_results["canary"] is False


# --- endpoints ---------------------------------------------------------------------


def _headers(key: str, *, recovery: bool = True) -> dict[str, str]:
    headers = {"X-Operator-Token": OPERATOR, "Idempotency-Key": key}
    if recovery:
        headers["X-Recovery-Token"] = RECOVERY
    return headers


def _healthy_app(tmp_path, **overrides):
    _healthy_components(tmp_path)
    options = dict(
        operator_token=OPERATOR,
        recovery_token=RECOVERY,
        paper_db_path=tmp_path / "paper.sqlite3",
        health_watchdog_enabled=True,
    )
    options.update(overrides)
    return create_app(**options)


def test_endpoint_promotion_is_blocked_until_the_soak_completes(tmp_path) -> None:
    with TestClient(_healthy_app(tmp_path)) as client:
        recovered = client.post(RECOVER_URL, json={"reason": REASON}, headers=_headers("recover"))
        assert recovered.status_code == 200
        assert recovered.json()["stage"] == "CANARY"
        assert client.get("/api/v1/risk").json()["safety_state"] == "REDUCED_RISK"
        # The default policy soak is 300s: an immediate promotion is
        # refused, names the canary gate, and does not move the state.
        blocked = client.post(
            PROMOTE_URL, json={"reason": "soak complete"}, headers=_headers("promote")
        )
        assert blocked.status_code == 409
        assert "canary" in blocked.json()["detail"]
        assert client.get("/api/v1/risk").json()["safety_state"] == "REDUCED_RISK"


def test_endpoint_promotion_succeeds_when_every_gate_is_green(tmp_path) -> None:
    # A deployment whose policy sets a zero minimum soak: every other
    # gate is genuinely green on this healthy fixture, so the explicit
    # promotion step verifies the quorum and lands at NORMAL.
    app = _healthy_app(tmp_path, risk_policy=PaperRiskPolicy(recovery_canary_min_soak_seconds=0))
    with TestClient(app) as client:
        recovered = client.post(RECOVER_URL, json={"reason": REASON}, headers=_headers("recover"))
        assert recovered.status_code == 200
        promoted = client.post(
            PROMOTE_URL, json={"reason": "soak complete"}, headers=_headers("promote")
        )
        assert promoted.status_code == 200
        receipt = promoted.json()
        assert receipt["stage"] == "PROMOTED"
        assert receipt["safety_state"] == "NORMAL"
        assert receipt["canary_receipt_id"] == recovered.json()["receipt_id"]
        verdict = json.loads(receipt["recovery_verifier_promotion"])
        assert verdict["mode"] == "ENFORCED"
        assert verdict["verdict"] == "VERIFIED"
        assert verdict["failed_gates"] == []
        assert client.get("/api/v1/risk").json()["safety_state"] == "NORMAL"
        # Idempotent replay returns the identical promotion receipt.
        replay = client.post(
            PROMOTE_URL, json={"reason": "soak complete"}, headers=_headers("promote")
        )
        assert replay.status_code == 200
        assert replay.json() == receipt
        # A fresh promotion finds no active canary.
        again = client.post(
            PROMOTE_URL, json={"reason": "soak complete"}, headers=_headers("promote-2")
        )
        assert again.status_code == 409


def test_promote_requires_the_recovery_token_and_an_active_canary() -> None:
    client = TestClient(create_app(operator_token=OPERATOR, recovery_token=RECOVERY))
    missing = client.post(
        PROMOTE_URL, json={"reason": "soak complete"}, headers=_headers("p", recovery=False)
    )
    assert missing.status_code == 401
    refused = client.post(PROMOTE_URL, json={"reason": "soak complete"}, headers=_headers("p"))
    assert refused.status_code == 409

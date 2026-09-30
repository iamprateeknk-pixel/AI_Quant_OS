import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.contracts import RiskDecision, TradeIntent
from ai_quant_os.decision_gateway import DecisionGateway, require_current_decision
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.risk import RiskKernel, RiskLimits
from ai_quant_os.world_state import WorldStateEngine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def world():
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    engine.record_bars(
        tuple(
            Bar(
                "EURUSD",
                NOW - timedelta(seconds=3 - i),
                Decimal("1.1"),
                Decimal("1.1"),
                Decimal("1.1"),
                Decimal("1.1"),
                Decimal(1),
            )
            for i in range(4)
        ),
        now=NOW,
    )
    return engine.snapshot(
        (MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.1001"), NOW, "fixture", True),),
        now=NOW,
    )


class Provider:
    provider_id = "FIXTURE"
    provider_version = "1"

    def __init__(self, changes=None, fails=False, slow=False):
        self.changes = changes or {}
        self.fails = fails
        self.slow = slow
        self.cancelled = False

    async def classify(self, snapshot, instrument_id):
        if self.fails:
            raise RuntimeError("private provider error must not leak")
        if self.slow:
            try:
                await asyncio.sleep(10)
            finally:
                self.cancelled = True
        item = next(row for row in snapshot.instruments if row.instrument_id == instrument_id)
        return {
            "schema_version": "1.0",
            "world_state_version": snapshot.world_state_version,
            "instrument_id": instrument_id,
            "classification": "RANGING",
            "confidence": "0.95",
            "created_at": NOW,
            "expires_at": NOW + timedelta(seconds=3),
            "evidence_refs": item.evidence_refs,
            "calibration_ref": None,
            **self.changes,
        }


def classify(provider=None, clock=lambda: NOW):
    return asyncio.run(
        DecisionGateway(primary=provider, clock=clock, timeout_seconds=0.01).classify(
            world(), instrument_id="EURUSD", task_id="task"
        )
    )


def test_no_primary_uses_quantitative_fallback_and_escalates_uncalibrated_result():
    result = classify()
    assert result.model_route == "QUANTITATIVE"
    assert result.output.classification == "RANGING"
    assert result.output.next_route == "DEEP_REVIEW"
    assert result.requires_escalation is True
    assert result.confidence == 0
    assert "PRIMARY_NOT_CONFIGURED" in result.output.fallback_reasons
    assert result.evidence_refs
    assert result.expires_at <= world().valid_until


def test_provider_failure_and_timeout_fall_back_without_private_error_details():
    failure = classify(Provider(fails=True))
    assert failure.model_route == "QUANTITATIVE"
    assert "PRIMARY_FAILED" in failure.output.fallback_reasons
    assert "private" not in failure.model_dump_json()
    slow = Provider(slow=True)
    result = classify(slow)
    assert "PRIMARY_TIMEOUT" in result.output.fallback_reasons
    assert slow.cancelled is True


@pytest.mark.parametrize(
    "changes",
    [
        {"world_state_version": "wrong"},
        {"instrument_id": "GBPUSD"},
        {"expires_at": NOW},
        {"created_at": NOW + timedelta(seconds=1)},
        {"confidence": "NaN"},
        {"classification": "BUY"},
        {"order_command": {}},
        {"evidence_refs": ["invented:source"]},
    ],
)
def test_invalid_or_unbound_primary_output_falls_back(changes):
    result = classify(Provider(changes))
    assert result.model_route == "QUANTITATIVE"
    assert result.requires_escalation
    assert result.output.fallback_reasons


def test_low_confidence_and_unverified_calibration_require_review():
    low = classify(Provider({"confidence": "0.4"}))
    assert low.model_route == "FIXTURE"
    assert "LOW_CONFIDENCE" in low.output.fallback_reasons
    assert low.requires_escalation
    high = classify(Provider({"calibration_ref": "unverified-report"}))
    assert "UNCALIBRATED" in high.output.fallback_reasons
    assert high.requires_escalation


def test_expired_snapshots_and_consumed_decisions_are_rejected():
    with pytest.raises(ValueError, match="expired"):
        classify(clock=lambda: NOW + timedelta(seconds=6))
    result = classify()
    require_current_decision(result, world_state_version=world().world_state_version, now=NOW)
    with pytest.raises(ValueError, match="expired"):
        require_current_decision(
            result, world_state_version=world().world_state_version, now=result.expires_at
        )
    with pytest.raises(ValueError, match="world"):
        require_current_decision(result, world_state_version="different", now=NOW)


def test_schema_valid_classifier_output_cannot_authorize_an_order():
    result = classify()
    intent = TradeIntent(
        "intent",
        "fixture",
        "1",
        "EURUSD",
        "BUY",
        Decimal(1),
        Decimal(1),
        Decimal("0.1"),
        Decimal(0),
        NOW + timedelta(seconds=3),
        result.world_state_version,
        result.evidence_refs,
        NOW,
    )
    forged = RiskDecision(
        result.trace_id, intent.intent_id, "APPROVE", Decimal(1), (), (), "fake", NOW
    )
    kernel = RiskKernel(RiskLimits(Decimal(1000), Decimal(1500), 5))
    with pytest.raises(ValueError, match="not issued"):
        with kernel.authorization(intent, forged):
            pytest.fail("classification granted authority")


def test_api_classifies_without_creating_orders_and_rejects_extra_authority_fields():
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    url = "/api/v1/paper/decisions/classify"
    payload = {"instrument_id": "EURUSD", "task_id": "task"}
    assert client.post(url, json=payload).status_code == 401
    response = client.post(url, json=payload, headers={"X-Operator-Token": "fixture-token"})
    assert response.status_code == 200
    assert response.json()["output"]["next_route"] == "WAIT_FOR_DATA"
    assert response.json()["requires_escalation"] is True
    assert client.get("/api/v1/orders").json()["orders"] == []
    assert (
        client.post(
            url,
            json={**payload, "approved_exposure": "100"},
            headers={"X-Operator-Token": "fixture-token"},
        ).status_code
        == 422
    )


def test_evidence_expiring_while_provider_runs_rejects_the_result():
    time = [NOW]

    class AdvancingProvider(Provider):
        async def classify(self, snapshot, instrument_id):
            result = await super().classify(snapshot, instrument_id)
            time[0] = NOW + timedelta(seconds=6)
            return result

    with pytest.raises(ValueError, match="expired"):
        classify(AdvancingProvider(), clock=lambda: time[0])


def test_disagreement_is_retained_for_review_and_result_expiry_is_bounded():
    result = classify(Provider({"classification": "TRENDING"}))
    assert result.contradictions == ("QUANTITATIVE_REGIME:RANGING",)
    assert result.expires_at == NOW + timedelta(seconds=3)
    assert result.requires_escalation


def test_api_audits_classification_route_and_never_accepts_client_provider_settings():
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    headers = {"X-Operator-Token": "fixture-token"}
    response = client.post(
        "/api/v1/paper/decisions/classify",
        headers=headers,
        json={"instrument_id": "EURUSD", "task_id": "task"},
    )
    assert response.status_code == 200
    envelope = response.json()
    events = client.get("/api/v1/audit").json()["events"]
    assert events[-1]["trace_id"] == envelope["trace_id"]
    assert events[-1]["world_state_version"] == envelope["world_state_version"]
    assert events[-1]["reason"] == "WAIT_FOR_DATA"
    assert client.get("/api/v1/audit/integrity").json()["valid"] is True
    assert (
        client.post(
            "/api/v1/paper/decisions/classify",
            headers=headers,
            json={"instrument_id": "EURUSD", "task_id": "task", "model_route": "LIVE"},
        ).status_code
        == 422
    )


def test_api_rejects_result_that_expires_during_audit_persistence(monkeypatch):
    from ai_quant_os.paper_state_store import PaperStateStore

    time = [NOW]
    original = PaperStateStore.record_event

    def delayed_record(self, key, event):
        original(self, key, event)
        if event["action"] == "PAPER_CLASSIFICATION":
            time[0] = NOW + timedelta(seconds=6)

    monkeypatch.setattr(PaperStateStore, "record_event", delayed_record)
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: time[0]))
    result = client.post(
        "/api/v1/paper/decisions/classify",
        headers={"X-Operator-Token": "fixture-token"},
        json={"instrument_id": "EURUSD", "task_id": "task"},
    )
    assert result.status_code == 409
    assert "expired" in result.json()["detail"]

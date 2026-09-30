import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from ai_quant_os.decision_gateway import DecisionGateway
from ai_quant_os.jev_adapter import JevClassifier, jev_from_environment
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
MODEL = "jev-1.13.0"


def world():
    return WorldStateEngine(PAPER_INSTRUMENTS).snapshot(
        (MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.1001"), NOW, "fixture", True),),
        now=NOW,
    )


def response(**changes):
    return {
        "model": MODEL,
        "answers": {
            "regime": {
                "type": "choice",
                "choice": "RANGING",
                "confidence": 0.9,
                "probabilities": {
                    "TRENDING": 0.1,
                    "RANGING": 0.7,
                    "HIGH_VOLATILITY": 0.1,
                    "UNKNOWN": 0.1,
                },
            }
        },
        "usage": {"input_tokens": 100, "output_tokens": 0},
        **changes,
    }


def test_adapter_uses_official_choice_contract_and_preserves_probability_semantics():
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["authorization"] == "Bearer fixture-only-key"
        body = json.loads(request.content)
        assert body["model"] == MODEL
        assert body["questions"]["regime"]["type"] == "choice"
        assert set(body["questions"]["regime"]["criteria"]) == {
            "TRENDING",
            "RANGING",
            "HIGH_VOLATILITY",
            "UNKNOWN",
        }
        assert "api_key" not in json.dumps(body)
        assert "orders" not in json.dumps(body)
        return httpx.Response(200, json=response())

    adapter = JevClassifier(
        api_key="fixture-only-key", model=MODEL, transport=httpx.MockTransport(handle)
    )
    result = asyncio.run(
        DecisionGateway(primary=adapter, clock=lambda: NOW).classify(
            world(), instrument_id="EURUSD", task_id="task"
        )
    )
    assert result.model_route == "JEV"
    assert result.provider_version == MODEL
    assert result.confidence == Decimal("0.7")
    assert result.requires_escalation
    assert len(requests) == 1
    assert adapter.status()["input_tokens"] == 100
    assert "fixture-only-key" not in repr(adapter) + json.dumps(adapter.status())


@pytest.mark.parametrize(
    "payload",
    [
        response(model="jev-unexpected"),
        response(answers={"regime": {"type": "noul", "noul": 0.9}}),
        response(
            answers={
                "regime": {
                    "type": "choice",
                    "choice": "BUY",
                    "confidence": 0.9,
                    "probabilities": {"BUY": 1},
                }
            }
        ),
        response(
            answers={
                "regime": {
                    "type": "choice",
                    "choice": "RANGING",
                    "confidence": 0.9,
                    "probabilities": {
                        "TRENDING": 0.7,
                        "RANGING": 0.1,
                        "HIGH_VOLATILITY": 0.1,
                        "UNKNOWN": 0.1,
                    },
                }
            }
        ),
        response(usage={"input_tokens": -1, "output_tokens": 0}),
    ],
)
def test_invalid_provider_responses_fall_back(payload):
    adapter = JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    result = asyncio.run(
        DecisionGateway(primary=adapter, clock=lambda: NOW).classify(
            world(), instrument_id="EURUSD", task_id="task"
        )
    )
    assert result.model_route == "QUANTITATIVE"
    assert "PRIMARY_FAILED" in result.output.fallback_reasons


def test_circuit_and_request_budget_prevent_additional_calls():
    clock = [0.0]
    calls = []

    def unavailable(request):
        calls.append(request)
        return httpx.Response(503, text="private provider detail")

    adapter = JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        failure_threshold=2,
        cooldown_seconds=10,
        max_requests=3,
        monotonic_clock=lambda: clock[0],
        transport=httpx.MockTransport(unavailable),
    )
    for _ in range(3):
        with pytest.raises(ValueError):
            asyncio.run(adapter.classify(world(), "EURUSD"))
    assert len(calls) == 2
    assert adapter.status()["circuit_state"] == "OPEN"
    clock[0] = 11
    with pytest.raises(ValueError):
        asyncio.run(adapter.classify(world(), "EURUSD"))
    assert len(calls) == 3
    with pytest.raises(ValueError):
        asyncio.run(adapter.classify(world(), "EURUSD"))
    assert len(calls) == 3
    assert adapter.status()["budget_exhausted"] is True


def test_redirects_and_response_body_limits_are_rejected_without_retries():
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://example.com/private"})

    adapter = JevClassifier(
        api_key="fixture-only-key", model=MODEL, transport=httpx.MockTransport(redirect)
    )
    with pytest.raises(ValueError):
        asyncio.run(adapter.classify(world(), "EURUSD"))
    assert len(calls) == 1
    huge = JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        max_response_bytes=100,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 101)),
    )
    with pytest.raises(ValueError):
        asyncio.run(huge.classify(world(), "EURUSD"))


def test_environment_is_disabled_by_default_and_rejects_partial_or_alias_configuration(monkeypatch):
    monkeypatch.delenv("AI_QUANT_JEV_ENABLED", raising=False)
    monkeypatch.setenv("AI_QUANT_JEV_API_KEY", "fixture-only-key")
    assert jev_from_environment() is None
    monkeypatch.setenv("AI_QUANT_JEV_ENABLED", "true")
    monkeypatch.delenv("AI_QUANT_JEV_MODEL", raising=False)
    with pytest.raises(ValueError):
        jev_from_environment()
    monkeypatch.setenv("AI_QUANT_JEV_MODEL", "jev-latest")
    with pytest.raises(ValueError):
        jev_from_environment()
    monkeypatch.setenv("AI_QUANT_JEV_MODEL", MODEL)
    adapter = jev_from_environment()
    assert adapter.provider_version == MODEL
    assert "fixture-only-key" not in repr(adapter)


def test_usage_is_recorded_even_if_response_model_is_rejected():
    adapter = JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response(model="unexpected"))
        ),
    )
    with pytest.raises(ValueError):
        asyncio.run(adapter.classify(world(), "EURUSD"))
    assert adapter.status()["input_tokens"] == 100
    assert adapter.status()["unreported_usage_count"] == 0
    failed = JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    with pytest.raises(ValueError):
        asyncio.run(failed.classify(world(), "EURUSD"))
    assert failed.status()["unreported_usage_count"] == 1


def test_request_budget_is_atomic_under_concurrent_calls():
    calls = []

    async def handle(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=response())

    adapter = JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        max_requests=1,
        transport=httpx.MockTransport(handle),
    )

    async def invoke():
        return await asyncio.gather(
            *(adapter.classify(world(), "EURUSD") for _ in range(8)), return_exceptions=True
        )

    results = asyncio.run(invoke())
    assert len(calls) == 1
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert adapter.status()["budget_exhausted"]


def test_api_model_budget_failure_does_not_disable_risk_kernel():
    from fastapi.testclient import TestClient
    from datetime import timedelta
    from ai_quant_os.api import create_app

    adapter = JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        max_requests=1,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response())),
    )
    client = TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, fast_classifier=adapter)
    )
    headers = {"X-Operator-Token": "fixture-token"}
    assert (
        client.post(
            "/api/v1/paper/quotes",
            headers=headers,
            json={
                "instrument_id": "EURUSD",
                "bid": "1.1",
                "ask": "1.1001",
                "observed_at": NOW.isoformat(),
                "world_state_version": "fixture",
            },
        ).status_code
        == 200
    )
    for index in range(2):
        decision = client.post(
            "/api/v1/paper/decisions/classify",
            headers=headers,
            json={"instrument_id": "EURUSD", "task_id": str(index)},
        )
        assert decision.status_code == 200
    assert decision.json()["model_route"] == "QUANTITATIVE"
    status = client.get("/api/v1/decisions/provider").json()
    assert status["budget_exhausted"] is True
    assert "fixture-only-key" not in json.dumps(status)
    trade = {
        "intent_id": "unaffected",
        "strategy_id": "fixture",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "100",
        "risk_budget": "10",
        "max_spread": "0.1",
        "max_slippage": "0",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "fixture",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }
    order = client.post(
        "/api/v1/paper/intents", headers={**headers, "Idempotency-Key": "unaffected"}, json=trade
    )
    assert order.status_code == 200
    assert order.json()["status"] == "ACCEPTED"


def test_gateway_timeout_cancels_io_and_tracks_unreported_usage():
    cancelled = []

    async def slow(request):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    adapter = JevClassifier(
        api_key="fixture-only-key", model=MODEL, transport=httpx.MockTransport(slow)
    )
    result = asyncio.run(
        DecisionGateway(primary=adapter, clock=lambda: NOW, timeout_seconds=0.01).classify(
            world(), instrument_id="EURUSD", task_id="task"
        )
    )
    assert "PRIMARY_TIMEOUT" in result.output.fallback_reasons
    assert cancelled == [True]
    assert adapter.status()["failure_count"] == 1
    assert adapter.status()["unreported_usage_count"] == 1


def test_old_inflight_success_cannot_close_newly_opened_circuit():
    calls = []

    async def handle(request):
        calls.append(request)
        number = len(calls)
        await asyncio.sleep(0.01 if number < 3 else 0.03)
        return httpx.Response(503) if number < 3 else httpx.Response(200, json=response())

    adapter = JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        failure_threshold=2,
        monotonic_clock=lambda: 0,
        transport=httpx.MockTransport(handle),
    )

    async def invoke():
        return await asyncio.gather(
            *(adapter.classify(world(), "EURUSD") for _ in range(3)), return_exceptions=True
        )

    results = asyncio.run(invoke())
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert adapter.status()["circuit_state"] == "OPEN"


def test_cooldown_allows_one_probe_and_success_closes_circuit():
    clock = [0.0]
    calls = []

    async def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=response())

    adapter = JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        failure_threshold=1,
        cooldown_seconds=10,
        monotonic_clock=lambda: clock[0],
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(ValueError):
        asyncio.run(adapter.classify(world(), "EURUSD"))
    clock[0] = 11

    async def invoke():
        return await asyncio.gather(
            adapter.classify(world(), "EURUSD"),
            adapter.classify(world(), "EURUSD"),
            return_exceptions=True,
        )

    results = asyncio.run(invoke())
    assert len(calls) == 2
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert adapter.status()["circuit_state"] == "CLOSED"

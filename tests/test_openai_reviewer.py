import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from ai_quant_os.openai_reviewer import OpenAIReviewer, reviewer_from_environment
from ai_quant_os.decision_gateway import DecisionGateway
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine
from decimal import Decimal
from fastapi.testclient import TestClient
from ai_quant_os.api import create_app

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
MODEL = "gpt-4o-2024-08-06"


def response(request, **changes):
    evidence = json.loads(json.loads(request.content)["input"])
    output = {
        "classification": "RANGING",
        "confidence": 0.8,
        "evidence_refs": evidence["instrument"]["evidence_refs"],
        "contradictions": [],
        "rationale": "Fixture review.",
    }
    return {
        "id": "resp_fixture",
        "model": MODEL,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [
            {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(output), "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        **changes,
    }


async def inputs():
    state = WorldStateEngine(PAPER_INSTRUMENTS).snapshot(
        (MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.1001"), NOW, "fixture", True),),
        now=NOW,
    )
    decision = await DecisionGateway(clock=lambda: NOW).classify(
        state, instrument_id="EURUSD", task_id="fixture"
    )
    # Request fixture exercises direct reviewer input, even when the baseline needs more bars.
    return state, decision


def test_official_request_shape_and_locally_bound_review():
    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer fixture-key"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["tools"] == []
        assert body["max_output_tokens"] == 1024
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        evidence = json.loads(body["input"])
        assert "orders" not in evidence
        assert "task_id" not in evidence
        return httpx.Response(200, json=response(request))

    reviewer = OpenAIReviewer(
        api_key="fixture-key", model=MODEL, transport=httpx.MockTransport(handler)
    )

    async def invoke():
        state, decision = await inputs()
        item = await reviewer.review(state, decision)
        assert item.parent_trace_id == decision.trace_id
        assert item.world_state_version == state.world_state_version
        assert item.expires_at == decision.expires_at
        assert item.confidence == Decimal("0.8")

    asyncio.run(invoke())
    assert reviewer.status()["input_tokens"] == 100
    assert "fixture-key" not in str(reviewer.status()) + repr(reviewer)


@pytest.mark.parametrize(
    "change",
    [
        {"model": "moving-alias"},
        {"status": "incomplete"},
        {"output": [{"type": "function_call", "name": "place_order"}]},
        {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "refusal", "refusal": "private refusal"}],
                }
            ]
        },
        {"usage": {"input_tokens": -1, "output_tokens": 0, "total_tokens": 0}},
    ],
)
def test_invalid_or_refused_response_is_sanitized_and_counted(change):
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response(request, **change))
        ),
    )

    async def invoke():
        state, decision = await inputs()
        with pytest.raises(ValueError, match="OpenAI review unavailable or invalid"):
            await reviewer.review(state, decision)

    asyncio.run(invoke())
    assert reviewer.status()["failure_count"] == 1


def test_request_cap_and_circuit_prevent_further_http_calls():
    calls = []
    time = [0.0]

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        max_requests=3,
        failure_threshold=2,
        cooldown_seconds=10,
        monotonic_clock=lambda: time[0],
        transport=httpx.MockTransport(handler),
    )

    async def invoke():
        state, decision = await inputs()
        for _ in range(3):
            with pytest.raises(ValueError):
                await reviewer.review(state, decision)
        assert len(calls) == 2
        time[0] = 11
        for _ in range(2):
            with pytest.raises(ValueError):
                await reviewer.review(state, decision)

    asyncio.run(invoke())
    assert len(calls) == 3
    assert reviewer.status()["budget_exhausted"] is True
    assert reviewer.status()["unreported_usage_count"] == 3


def test_disabled_environment_and_explicit_pinned_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_QUANT_REVIEW_ENABLED", raising=False)
    assert reviewer_from_environment() is None
    monkeypatch.setenv("AI_QUANT_REVIEW_ENABLED", "true")
    monkeypatch.setenv("AI_QUANT_REVIEW_API_KEY", "fixture-key")
    monkeypatch.setenv("AI_QUANT_REVIEW_MODEL", "gpt-4o")
    with pytest.raises(ValueError, match="pinned"):
        reviewer_from_environment()
    monkeypatch.setenv("AI_QUANT_REVIEW_MODEL", MODEL)
    from test_provider_spend import policy

    declared = tmp_path / "spend-policy.json"
    declared.write_bytes(policy().encode())
    monkeypatch.setenv("AI_QUANT_REVIEW_SPEND_PATH", str(tmp_path / "spend.sqlite3"))
    monkeypatch.setenv("AI_QUANT_REVIEW_SPEND_POLICY", str(declared))
    monkeypatch.setenv("AI_QUANT_REVIEW_OWNER", "A02@0.2.0")
    assert reviewer_from_environment().provider_version == MODEL


@pytest.mark.parametrize("status,limit", [(302, 65536), (200, 100)])
def test_redirect_and_response_size_are_rejected_without_retry(status, limit):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json=response(request), headers={"Location": "https://example.invalid/private"}
        )

    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        max_response_bytes=limit,
        transport=httpx.MockTransport(handler),
    )

    async def invoke():
        state, decision = await inputs()
        with pytest.raises(ValueError):
            await reviewer.review(state, decision)

    asyncio.run(invoke())
    assert len(calls) == 1


def test_concurrent_request_cap_and_cancellation_accounting():
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(10)
        return httpx.Response(200, json=response(request))

    reviewer = OpenAIReviewer(
        api_key="fixture-key", model=MODEL, max_requests=1, transport=httpx.MockTransport(handler)
    )

    async def invoke():
        state, decision = await inputs()
        results = await asyncio.gather(
            *(asyncio.wait_for(reviewer.review(state, decision), timeout=0.01) for _ in range(5)),
            return_exceptions=True,
        )
        assert all(isinstance(result, (ValueError, TimeoutError)) for result in results)

    asyncio.run(invoke())
    assert len(calls) == 1
    assert reviewer.status()["failure_count"] == 1
    assert reviewer.status()["unreported_usage_count"] == 1


def test_older_success_cannot_close_newly_opened_circuit():
    calls = []
    started = asyncio.Event()
    release_success = asyncio.Event()

    async def handler(request):
        index = len(calls)
        calls.append(request)
        if index == 0:
            started.set()
            await release_success.wait()
        return httpx.Response(200, json=response(request)) if index == 0 else httpx.Response(503)

    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        failure_threshold=2,
        transport=httpx.MockTransport(handler),
    )

    async def invoke():
        state, decision = await inputs()
        older = asyncio.create_task(reviewer.review(state, decision))
        await asyncio.wait_for(started.wait(), timeout=2)
        results = await asyncio.gather(
            *(reviewer.review(state, decision) for _ in range(2)), return_exceptions=True
        )
        assert all(isinstance(result, ValueError) for result in results)
        assert reviewer.status()["circuit_state"] == "OPEN"
        release_success.set()
        assert (await older).classification == "RANGING"

    asyncio.run(invoke())
    assert reviewer.status()["circuit_state"] == "OPEN"


def test_api_review_is_archived_and_status_is_sanitized():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response(request))

    reviewer = OpenAIReviewer(
        api_key="fixture-key", model=MODEL, transport=httpx.MockTransport(handler)
    )
    client = TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, deep_reviewer=reviewer)
    )
    headers = {"X-Operator-Token": "fixture-token"}
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
    )
    from datetime import timedelta

    client.post(
        "/api/v1/paper/world-state/bars",
        headers=headers,
        json={
            "bars": [
                {
                    "instrument_id": "EURUSD",
                    "observed_at": (NOW - timedelta(seconds=i)).isoformat(),
                    "open": "1.1",
                    "high": "1.1",
                    "low": "1.1",
                    "close": "1.1",
                    "volume": "1",
                }
                for i in range(3)
            ]
        },
    )
    result = client.post(
        "/api/v1/paper/decisions/classify",
        headers=headers,
        json={"instrument_id": "EURUSD", "task_id": "fixture"},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["deep_review"]["output"]["status"] == "COMPLETED"
    assert body["deep_review"]["requires_escalation"] is True
    assert (
        client.get(f"/api/v1/agents/runs/{body['trace_id']}").json()["run"]["deep_review"]
        == body["deep_review"]
    )
    status = client.get("/api/v1/decisions/provider")
    assert status.json()["deep_review"]["input_tokens"] == 100
    assert "fixture-key" not in status.text
    assert len(calls) == 1
    assert client.get("/api/v1/orders").json()["orders"] == []


def test_only_one_circuit_probe_is_admitted_after_cooldown():
    time = [0.0]
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        return (
            httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=response(request))
        )

    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        failure_threshold=1,
        cooldown_seconds=1,
        monotonic_clock=lambda: time[0],
        transport=httpx.MockTransport(handler),
    )

    async def invoke():
        state, decision = await inputs()
        with pytest.raises(ValueError):
            await reviewer.review(state, decision)
        time[0] = 2
        results = await asyncio.gather(
            *(reviewer.review(state, decision) for _ in range(2)), return_exceptions=True
        )
        assert sum(isinstance(result, ValueError) for result in results) == 1

    asyncio.run(invoke())
    assert len(calls) == 2
    assert reviewer.status()["circuit_state"] == "CLOSED"


@pytest.mark.parametrize(
    "changes", [{"evidence_refs": ["invented"]}, {"confidence": "NaN"}, {"approved_exposure": 1000}]
)
def test_invalid_structured_text_is_rejected(changes):
    def handler(request):
        result = response(request)
        text = json.loads(result["output"][0]["content"][0]["text"])
        result["output"][0]["content"][0]["text"] = json.dumps({**text, **changes})
        return httpx.Response(200, json=result)

    reviewer = OpenAIReviewer(
        api_key="fixture-key", model=MODEL, transport=httpx.MockTransport(handler)
    )

    async def invoke():
        state, decision = await inputs()
        with pytest.raises(ValueError, match="unavailable or invalid"):
            await reviewer.review(state, decision)

    asyncio.run(invoke())
    assert reviewer.status()["failure_count"] == 1
    assert reviewer.status()["input_tokens"] == 100

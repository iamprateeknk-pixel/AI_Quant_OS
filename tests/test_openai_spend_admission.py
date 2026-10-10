"""Actual adapter HTTP fixture: counted input + bounded output before generation."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from ai_quant_os.openai_reviewer import OpenAIReviewer, reviewer_from_environment
from ai_quant_os.provider_call_admission import (
    ProviderOwner,
    ProviderCallAdmission,
    AdmittedReviewer,
)
from ai_quant_os.agent_call_budgets import AgentCallBudget
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.provider_spend_store import ProviderSpendStore
from test_openai_reviewer import inputs, response, MODEL, NOW
from test_provider_spend import policy


def configured(tmp_path, handler, **changes):
    store = ProviderSpendStore(tmp_path / "spend.db", policy(tokens=2000))
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        transport=httpx.MockTransport(handler),
        spend_store=store,
        spend_owner=ProviderOwner("A02", "0.2.0"),
        utc_clock=lambda: NOW,
        **changes,
    )
    return reviewer, store


def test_exact_preflight_request_reserved_before_generation_and_settled_before_decision(tmp_path):
    requests = []
    holder = {}

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        if str(request.url).endswith("/input_tokens"):
            assert set(body) == {"model", "input", "instructions", "text", "tools"}
            return httpx.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 100}
            )
        counted = json.loads(requests[0].content)
        assert all(body[key] == value for key, value in counted.items())
        assert body["truncation"] == "disabled"
        status = holder["store"].status(NOW)
        row = next(row for row in status["agents"] if row["agent_id"] == "A02")
        assert row["pending_count"] == 1 and row["daily_tokens"] == 1124
        return httpx.Response(200, json=response(request))

    reviewer, store = configured(tmp_path, handler)
    holder["store"] = store

    async def invoke():
        world, decision = await inputs()
        await reviewer.review(world, decision)

    asyncio.run(invoke())
    row = next(row for row in store.status(NOW)["agents"] if row["agent_id"] == "A02")
    assert row["daily_tokens"] == 120 and row["pending_count"] == 0
    assert row["daily_nano_usd"] == 360
    assert len(requests) == 2
    assert reviewer.status()["spending"]["state"] == "ENFORCED"


@pytest.mark.parametrize(
    "bad_count",
    [
        {"input_tokens": True, "object": "response.input_tokens"},
        {"input_tokens": -1, "object": "response.input_tokens"},
        {"input_tokens": 100, "object": "other"},
    ],
)
def test_invalid_token_count_never_dispatches_generation(tmp_path, bad_count):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=bad_count)

    reviewer, store = configured(tmp_path, handler)

    async def invoke():
        world, decision = await inputs()
        with pytest.raises(ValueError):
            await reviewer.review(world, decision)

    asyncio.run(invoke())
    assert len(requests) == 1
    assert all(row["pending_count"] == 0 for row in store.status(NOW)["agents"])


def test_token_exhaustion_denies_generation_and_uncertain_request_never_refunds_hold(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if str(request.url).endswith("/input_tokens"):
            return httpx.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 100}
            )
        return httpx.Response(503)

    reviewer, store = configured(tmp_path, handler)

    async def invoke():
        world, decision = await inputs()
        for _ in range(2):
            with pytest.raises(ValueError):
                await reviewer.review(world, decision)

    asyncio.run(invoke())
    assert sum(not str(request.url).endswith("/input_tokens") for request in requests) == 1
    row = next(
        row
        for row in ProviderSpendStore(store.path, None).status(NOW + timedelta(days=40))["agents"]
        if row["agent_id"] == "A02"
    )
    assert row["pending_count"] == 1 and row["daily_tokens"] == 1124


@pytest.mark.parametrize(
    "changes,pending,tokens",
    [
        ({"status": "incomplete"}, 0, 120),
        ({"model": "moving-alias"}, 1, 1124),
        ({"usage": {"input_tokens": 101, "output_tokens": 20, "total_tokens": 121}}, 0, 121),
    ],
)
def test_invalid_candidate_reports_are_charged_and_wrong_identity_remains_unknown(
    tmp_path, changes, pending, tokens
):
    def handler(request):
        if str(request.url).endswith("/input_tokens"):
            return httpx.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 100}
            )
        return httpx.Response(200, json=response(request, **changes))

    reviewer, store = configured(tmp_path, handler)

    async def invoke():
        world, decision = await inputs()
        with pytest.raises(ValueError):
            await reviewer.review(world, decision)

    asyncio.run(invoke())
    row = next(row for row in store.status(NOW)["agents"] if row["agent_id"] == "A02")
    assert row["pending_count"] == pending and row["daily_tokens"] == tokens


def test_environment_partial_spending_configuration_cannot_silently_disable_admission(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AI_QUANT_REVIEW_ENABLED", "true")
    monkeypatch.setenv("AI_QUANT_REVIEW_API_KEY", "fixture-key")
    monkeypatch.setenv("AI_QUANT_REVIEW_MODEL", MODEL)
    monkeypatch.setenv("AI_QUANT_REVIEW_SPEND_PATH", str(tmp_path / "spend.db"))
    with pytest.raises(ValueError):
        reviewer_from_environment()


def test_enabled_environment_requires_persistent_spending_admission(monkeypatch):
    monkeypatch.setenv("AI_QUANT_REVIEW_ENABLED", "true")
    monkeypatch.setenv("AI_QUANT_REVIEW_API_KEY", "fixture-key")
    monkeypatch.setenv("AI_QUANT_REVIEW_MODEL", MODEL)
    monkeypatch.delenv("AI_QUANT_REVIEW_SPEND_PATH", raising=False)
    monkeypatch.delenv("AI_QUANT_REVIEW_SPEND_POLICY", raising=False)
    with pytest.raises(ValueError, match="spending"):
        reviewer_from_environment()


def test_changed_permission_during_preflight_stops_generation_and_owner_cannot_be_misattributed(
    tmp_path,
):
    registry = load_agent_catalog()
    calls = []

    def handler(request):
        calls.append(request)
        registry.set_lifecycle("A02", "0.2.0", "QUARANTINED")
        return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})

    reviewer, store = configured(tmp_path, handler)
    budget = AgentCallBudget(None, policy=None, clock=lambda: NOW)
    admission = ProviderCallAdmission(
        registry, budget, ProviderOwner("A02", "0.2.0"), clock=lambda: NOW
    )
    wrapper = AdmittedReviewer(reviewer, admission)

    async def invoke():
        world, decision = await inputs()
        with pytest.raises((ValueError, PermissionError)):
            await wrapper.review(world, decision)

    asyncio.run(invoke())
    assert len(calls) == 1 and all(row["pending_count"] == 0 for row in store.status(NOW)["agents"])
    with pytest.raises(ValueError):
        AdmittedReviewer(
            reviewer,
            ProviderCallAdmission(
                registry, budget, ProviderOwner("A03", "0.2.0"), clock=lambda: NOW
            ),
        )


def test_cancelled_generation_retains_full_reservation(tmp_path):
    calls = []
    started = asyncio.Event()

    async def handler(request):
        calls.append(request)
        if str(request.url).endswith("/input_tokens"):
            return httpx.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 100}
            )
        started.set()
        await asyncio.Event().wait()

    reviewer, store = configured(tmp_path, handler)

    async def invoke():
        world, decision = await inputs()
        task = asyncio.create_task(reviewer.review(world, decision))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(invoke())
    row = next(row for row in store.status(NOW)["agents"] if row["agent_id"] == "A02")
    assert len(calls) == 2 and row["pending_count"] == 1 and row["daily_tokens"] == 1124


def test_expiry_during_count_and_corrupt_archive_prevent_generation(tmp_path):
    time = [NOW]
    calls = []

    def handler(request):
        calls.append(request)
        time[0] = NOW + timedelta(seconds=30)
        return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})

    store = ProviderSpendStore(tmp_path / "spend.db", policy(tokens=2000))
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        transport=httpx.MockTransport(handler),
        spend_store=store,
        spend_owner=ProviderOwner("A02", "0.2.0"),
        utc_clock=lambda: time[0],
    )

    async def invoke():
        world, decision = await inputs()
        with pytest.raises(ValueError):
            await reviewer.review(world, decision)

    asyncio.run(invoke())
    assert len(calls) == 1
    assert all(row["pending_count"] == 0 for row in store.status(time[0])["agents"])
    store.pin.write_bytes(b"corrupt")
    asyncio.run(invoke())
    assert len(calls) == 1
    assert reviewer.status()["spending"]["state"] == "UNAVAILABLE"
    assert str(tmp_path) not in str(reviewer.status())


def test_environment_can_initialize_and_reopen_original_pinned_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_QUANT_REVIEW_ENABLED", "true")
    monkeypatch.setenv("AI_QUANT_REVIEW_API_KEY", "fixture-key")
    monkeypatch.setenv("AI_QUANT_REVIEW_MODEL", MODEL)
    path = tmp_path / "spend.db"
    policy_path = tmp_path / "policy.json"
    policy_path.write_bytes(policy().encode())
    monkeypatch.setenv("AI_QUANT_REVIEW_SPEND_PATH", str(path))
    monkeypatch.setenv("AI_QUANT_REVIEW_SPEND_POLICY", str(policy_path))
    monkeypatch.setenv("AI_QUANT_REVIEW_OWNER", "A02@0.2.0")
    assert reviewer_from_environment().spend_owner == ProviderOwner("A02", "0.2.0")
    monkeypatch.delenv("AI_QUANT_REVIEW_SPEND_POLICY")
    assert reviewer_from_environment().status()["spending"]["state"] == "ENFORCED"


@pytest.mark.parametrize("corrupt", [False, True])
def test_native_api_budget_denial_preserves_quantitative_result_and_emergency_control(
    tmp_path, corrupt
):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})

    store = ProviderSpendStore(tmp_path / "spend.db", policy(tokens=100))
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        transport=httpx.MockTransport(handler),
        spend_store=store,
        spend_owner=ProviderOwner("A02", "0.2.0"),
        utc_clock=lambda: NOW,
    )
    app = create_app(
        operator_token="fixture-token",
        clock=lambda: NOW,
        deep_reviewer=reviewer,
        review_owner=ProviderOwner("A02", "0.2.0"),
    )
    with TestClient(app) as client:
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
        assert (
            client.post(
                "/api/v1/paper/world-state/bars",
                headers=headers,
                json={
                    "bars": [
                        {
                            "instrument_id": "EURUSD",
                            "observed_at": (NOW - timedelta(seconds=index)).isoformat(),
                            "open": "1.1",
                            "high": "1.1",
                            "low": "1.1",
                            "close": "1.1",
                            "volume": "1",
                        }
                        for index in range(3)
                    ]
                },
            ).status_code
            == 200
        )
        if corrupt:
            store.pin.write_bytes(b"corrupt")
        result = client.post(
            "/api/v1/paper/decisions/classify",
            headers=headers,
            json={"instrument_id": "EURUSD", "task_id": "spend-denial"},
        )
        assert result.status_code == 200
        assert result.json()["model_route"] == "QUANTITATIVE"
        assert result.json()["deep_review"]["output"]["status"] == "FAILED"
        safety = client.post(
            "/api/v1/control/no-new-risk",
            headers={**headers, "Idempotency-Key": "spend-denial-control"},
            json={"reason": "fixture budget denial"},
        )
        assert safety.status_code == 200 and safety.json()["safety_state"] == "NO_NEW_RISK"
        state = client.get("/api/v1/decisions/provider").json()
        assert "fixture-key" not in str(state) and str(tmp_path) not in str(state)
        assert client.get("/api/v1/orders").json()["orders"] == []
    assert len(requests) == (0 if corrupt else 1)
    assert all(str(request.url).endswith("/input_tokens") for request in requests)

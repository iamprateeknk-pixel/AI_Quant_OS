"""One atomic eighteen-role budget across pinned models and both provider adapters."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from ai_quant_os.jev_adapter import JevClassifier, jev_from_environment
from ai_quant_os.jev_spend_policy import JEV_INPUT_CEILING
from ai_quant_os.openai_reviewer import OpenAIReviewer, reviewer_from_environment
from ai_quant_os.provider_call_admission import ProviderOwner
from ai_quant_os.provider_spend_store import ProviderSpendStore
from ai_quant_os.shared_spend_policy import SharedSpendPolicy, decode_spending_policy
from test_jev_spend_admission import policy as jev_policy
from test_jev_adapter import MODEL as JEV_MODEL, response as jev_response
from test_openai_reviewer import (
    MODEL as OPENAI_MODEL,
    NOW,
    inputs as review_inputs,
    response as openai_response,
)
from test_provider_spend import policy as openai_policy


def policy(tokens=200000, money=10000000, reserve=0):
    jev = jev_policy()
    reviewer = openai_policy(tokens=200000, money=10000000, reserve=0)
    rules = tuple(
        replace(
            row,
            daily_tokens=tokens,
            monthly_tokens=tokens * 2,
            daily_token_reserve=reserve,
            monthly_token_reserve=reserve * 2,
            daily_nano_usd=money,
            monthly_nano_usd=money * 2,
            daily_money_reserve=0,
            monthly_money_reserve=0,
        )
        for row in jev.rules
    )
    return SharedSpendPolicy(rules=rules, models=(reviewer, jev))


def reserve(store, model=OPENAI_MODEL, *, now=NOW, version="0.2.0", role="A02"):
    return store.reserve(
        agent_id=role,
        agent_version=version,
        model=model,
        request_sha256="a" * 64,
        input_tokens=JEV_INPUT_CEILING if model == JEV_MODEL else 100,
        max_output_tokens=0 if model == JEV_MODEL else 128,
        now=now,
    )


def counters(store, now=NOW):
    return next(row for row in store.status(now)["agents"] if row["agent_id"] == "A02")


def test_shared_policy_roundtrip_and_canonical_model_inventory():
    original = policy()
    assert SharedSpendPolicy.decode(original.encode()) == original
    assert decode_spending_policy(original.encode()) == original
    assert decode_spending_policy(jev_policy().encode()) == jev_policy()
    assert decode_spending_policy(openai_policy().encode()) == openai_policy()
    assert replace(original, models=tuple(reversed(original.models))).encode() == original.encode()
    for changes in (
        {"models": ()},
        {"models": (original.models[0],) * 2},
        {"rules": original.rules[:-1]},
    ):
        with pytest.raises(ValueError):
            replace(original, **changes)
    bad = json.loads(original.encode())
    bad["models"][0]["model"] = "moving-alias"
    with pytest.raises(ValueError):
        SharedSpendPolicy.decode(json.dumps(bad).encode())
    with pytest.raises(ValueError):
        SharedSpendPolicy.decode(original.encode().replace(b'"models":', b'"models":[],"models":'))


def test_additive_shared_schema_resolves_only_original_local_model_contracts():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    root = Path(__file__).resolve().parents[1] / "docs/schemas"
    names = ("provider-spend-policy.schema.json", "jev-input-spend-policy.schema.json")
    registry = Registry().with_resources(
        [
            (name, Resource.from_contents(json.loads((root / name).read_text(encoding="utf-8"))))
            for name in names
        ]
    )
    schema = json.loads((root / "shared-spend-policy.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=registry)
    value = json.loads(policy().encode())
    validator.validate(value)
    assert not validator.is_valid({**value, "models": []})
    assert not validator.is_valid({**value, "rules": [value["rules"][0]] * 18})
    unknown = {**value, "models": [{**value["models"][0], "unexpected": True}]}
    assert not validator.is_valid(unknown)


def test_nested_shared_policies_and_duplicate_model_ids_with_different_rates_are_rejected():
    value = json.loads(policy().encode())
    with pytest.raises(ValueError):
        SharedSpendPolicy.decode(json.dumps({**value, "models": [value]}).encode())
    first = value["models"][0]
    with pytest.raises(ValueError):
        SharedSpendPolicy.decode(
            json.dumps({**value, "models": [first, {**first, "input_nano_usd": 999}]}).encode()
        )


def test_cross_provider_role_ceiling_is_not_doubled_by_second_model_or_version(tmp_path):
    store = ProviderSpendStore(tmp_path / "shared.db", policy(tokens=JEV_INPUT_CEILING + 227))
    reserve(store, JEV_MODEL)
    with pytest.raises(PermissionError):
        reserve(store, version="0.1.0")
    assert counters(store)["daily_tokens"] == JEV_INPUT_CEILING
    assert counters(store)["pending_count"] == 1
    reopened = ProviderSpendStore(store.path, None)
    with pytest.raises(PermissionError):
        reserve(reopened, now=NOW + timedelta(days=40))
    assert counters(reopened, NOW + timedelta(days=40))["monthly_tokens"] == JEV_INPUT_CEILING


@pytest.mark.parametrize("limit", ["tokens", "money", "reserve"])
def test_concurrent_independent_connections_cannot_both_spend_last_shared_capacity(tmp_path, limit):
    settings = (
        {"tokens": JEV_INPUT_CEILING}
        if limit == "tokens"
        else {"money": JEV_INPUT_CEILING * 42}
        if limit == "money"
        else {"tokens": JEV_INPUT_CEILING + 100, "reserve": 100}
    )
    path = tmp_path / "shared.db"
    ProviderSpendStore(path, policy(**settings))

    def attempt(model):
        try:
            reserve(ProviderSpendStore(path, None), model)
            return True
        except PermissionError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, [JEV_MODEL, OPENAI_MODEL]))
    assert sum(results) == 1
    assert counters(ProviderSpendStore(path, None))["pending_count"] == 1


def test_native_semantics_remain_per_model_and_model_limits_also_apply(tmp_path):
    original = policy()
    reviewer = next(row for row in original.models if row.model == OPENAI_MODEL)
    reviewer = replace(
        reviewer,
        rules=tuple(
            replace(
                row,
                daily_tokens=228,
                monthly_tokens=228,
                daily_token_reserve=0,
                monthly_token_reserve=0,
            )
            for row in reviewer.rules
        ),
    )
    store = ProviderSpendStore(
        tmp_path / "shared.db", replace(original, models=(reviewer, jev_policy()))
    )
    first = reserve(store, JEV_MODEL)
    store.settle(first, input_tokens=123, output_tokens=99999, now=NOW)
    second = reserve(store)
    store.settle(second, input_tokens=100, output_tokens=128, now=NOW)
    assert counters(store)["daily_tokens"] == 351
    assert counters(store)["daily_nano_usd"] == 123 * 42 + 100 * 2 + 128 * 8
    with pytest.raises(PermissionError):
        reserve(store)
    reserve(store, JEV_MODEL)
    state = store.status(NOW, model=OPENAI_MODEL)
    assert state["admitted_model"] == OPENAI_MODEL
    assert state["model_accounting"] == "PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING"
    assert state["accounting"] == "SHARED_MODEL_COUNTED_TOKENS_AT_DECLARED_RATE_CEILING"
    assert state["model"] is None


def test_unknown_model_and_incorrect_jev_reservation_cannot_enter_shared_journal(tmp_path):
    store = ProviderSpendStore(tmp_path / "shared.db", policy())
    for model, inputs, outputs in [
        ("gpt-unregistered-2026-01-01", 100, 128),
        (JEV_MODEL, 100, 0),
        (JEV_MODEL, JEV_INPUT_CEILING, 128),
    ]:
        with pytest.raises(ValueError):
            store.reserve(
                agent_id="A02",
                agent_version="0.2.0",
                model=model,
                request_sha256="a" * 64,
                input_tokens=inputs,
                max_output_tokens=outputs,
                now=NOW,
            )
    assert counters(store)["pending_count"] == 0
    with pytest.raises(ValueError):
        store.status(NOW, model="jev-latest")


def test_breach_in_one_model_stops_every_model_and_role_after_restart(tmp_path):
    store = ProviderSpendStore(tmp_path / "shared.db", policy())
    identity = reserve(store, JEV_MODEL)
    with pytest.raises(PermissionError):
        store.settle(identity, input_tokens=JEV_INPUT_CEILING + 1, output_tokens=99, now=NOW)
    reopened = ProviderSpendStore(store.path, None)
    assert reopened.status(NOW)["state"] == "BREACHED"
    for model in (JEV_MODEL, OPENAI_MODEL):
        with pytest.raises(PermissionError):
            reserve(reopened, model, role="A03")


def test_multiple_pinned_reviewer_models_use_their_original_rates_in_one_role_budget(tmp_path):
    original = policy()
    first = next(row for row in original.models if row.model == OPENAI_MODEL)
    second = replace(first, model="gpt-fixture-2026-01-01", input_nano_usd=3, output_nano_usd=9)
    store = ProviderSpendStore(
        tmp_path / "shared.db", replace(original, models=(*original.models, second))
    )
    identity = reserve(store)
    store.settle(identity, input_tokens=100, output_tokens=20, now=NOW)
    another = store.reserve(
        agent_id="A02",
        agent_version="0.1.0",
        model=second.model,
        request_sha256="b" * 64,
        input_tokens=100,
        max_output_tokens=128,
        now=NOW,
    )
    store.settle(another, input_tokens=100, output_tokens=20, now=NOW)
    assert counters(store)["daily_tokens"] == 240
    assert counters(store)["daily_nano_usd"] == 840
    state = store.status(NOW, model=second.model)
    assert len(state["models"]) == 3
    model_row = next(row for row in state["model_agents"] if row["agent_id"] == "A02")
    assert model_row["daily_tokens"] == 120 and model_row["daily_nano_usd"] == 480


def test_original_model_inventory_and_aggregate_policy_cannot_be_replaced(tmp_path):
    original = policy()
    store = ProviderSpendStore(tmp_path / "shared.db", original)
    reserve(store)
    with pytest.raises(ValueError, match="immutable"):
        ProviderSpendStore(store.path, replace(original, models=original.models[:1]))
    with pytest.raises(ValueError, match="immutable"):
        ProviderSpendStore(
            store.path,
            replace(
                original,
                rules=tuple(
                    replace(row, daily_nano_usd=row.daily_nano_usd + 1) for row in original.rules
                ),
            ),
        )
    store.pin.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        reserve(store)
    with pytest.raises(ValueError):
        ProviderSpendStore(store.path, None)


def test_one_native_reviewer_hold_blocks_actual_jev_dispatch_until_native_settlement(tmp_path):
    shared = policy(tokens=JEV_INPUT_CEILING + 1123)
    store = ProviderSpendStore(tmp_path / "shared.db", shared)
    calls = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handle_openai(request):
        calls.append(str(request.url))
        if str(request.url).endswith("/input_tokens"):
            return httpx.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 100}
            )
        entered.set()
        await release.wait()
        return httpx.Response(200, json=openai_response(request))

    def handle_jev(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=jev_response())

    owner = ProviderOwner("A02", "0.2.0")
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=OPENAI_MODEL,
        spend_store=store,
        spend_owner=owner,
        utc_clock=lambda: NOW,
        transport=httpx.MockTransport(handle_openai),
    )
    classifier = JevClassifier(
        api_key="fixture-key",
        model=JEV_MODEL,
        spend_store=ProviderSpendStore(store.path, None),
        spend_owner=owner,
        utc_clock=lambda: NOW,
        transport=httpx.MockTransport(handle_jev),
    )

    async def invoke():
        world, decision = await review_inputs()
        review = asyncio.create_task(reviewer.review(world, decision))
        await entered.wait()
        try:
            with pytest.raises(ValueError):
                await classifier.classify(world, "EURUSD")
            assert calls == [
                "https://api.openai.com/v1/responses/input_tokens",
                "https://api.openai.com/v1/responses",
            ]
        finally:
            release.set()
        await review
        result = await classifier.classify(world, "EURUSD")
        assert result.classification == "RANGING"

    asyncio.run(invoke())
    assert counters(store)["daily_tokens"] == 220
    assert counters(store)["daily_nano_usd"] == 4560
    assert counters(store)["pending_count"] == 0
    assert (
        reviewer.spending_status()["policy_sha256"] == classifier.spending_status()["policy_sha256"]
    )


def test_shared_environment_loads_same_original_book_for_both_providers(tmp_path, monkeypatch):
    source = tmp_path / "policy.json"
    source.write_bytes(policy().encode())
    path = tmp_path / "shared.db"
    for prefix, model in [("REVIEW", OPENAI_MODEL), ("JEV", JEV_MODEL)]:
        monkeypatch.setenv(f"AI_QUANT_{prefix}_ENABLED", "true")
        monkeypatch.setenv(f"AI_QUANT_{prefix}_API_KEY", "fixture-key")
        monkeypatch.setenv(f"AI_QUANT_{prefix}_MODEL", model)
        monkeypatch.setenv(f"AI_QUANT_{prefix}_SPEND_PATH", str(path))
        monkeypatch.setenv(f"AI_QUANT_{prefix}_SPEND_POLICY", str(source))
    monkeypatch.setenv("AI_QUANT_REVIEW_OWNER", "A02@0.2.0")
    monkeypatch.setenv("AI_QUANT_CLASSIFY_OWNER", "A02@0.2.0")
    reviewer = reviewer_from_environment()
    classifier = jev_from_environment()
    assert (
        reviewer.spending_status()["policy_sha256"] == classifier.spending_status()["policy_sha256"]
    )
    assert reviewer.spending_status()["head_hash"] == classifier.spending_status()["head_hash"]
    assert "fixture-key" not in str(reviewer.status()) + str(classifier.status())
    monkeypatch.delenv("AI_QUANT_JEV_SPEND_POLICY")
    assert (
        jev_from_environment().spending_status()["policy_sha256"]
        == classifier.spending_status()["policy_sha256"]
    )


@pytest.mark.parametrize("cause", ["zero_aggregate_budget", "corrupt_original_pin"])
def test_actual_api_shared_budget_failures_preserve_quantitative_fallback_and_emergency_controls(
    tmp_path, cause
):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app

    store = ProviderSpendStore(
        tmp_path / "shared.db", policy(tokens=0 if cause == "zero_aggregate_budget" else 200000)
    )
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert str(request.url).endswith("/input_tokens"), "Generation escaped the shared quota"
        return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})

    owner = ProviderOwner("A02", "0.2.0")
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=OPENAI_MODEL,
        spend_store=store,
        spend_owner=owner,
        utc_clock=lambda: NOW,
        transport=httpx.MockTransport(handler),
    )
    classifier = JevClassifier(
        api_key="fixture-key",
        model=JEV_MODEL,
        spend_store=ProviderSpendStore(store.path, None),
        spend_owner=owner,
        utc_clock=lambda: NOW,
        transport=httpx.MockTransport(handler),
    )
    if cause == "corrupt_original_pin":
        store.pin.write_bytes(b"corrupt")
    with TestClient(
        create_app(
            operator_token="fixture-token",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.db",
            fast_classifier=classifier,
            deep_reviewer=reviewer,
            classification_owner=owner,
            review_owner=owner,
        )
    ) as client:
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
        result = client.post(
            "/api/v1/paper/decisions/classify",
            headers=headers,
            json={
                "instrument_id": "EURUSD",
                "task_id": "shared-budget-denial",
            },
        )
        assert result.status_code == 200
        assert result.json()["model_route"] == "QUANTITATIVE"
        assert result.json()["deep_review"]["output"]["status"] == "FAILED"
        safety = client.post(
            "/api/v1/control/no-new-risk",
            headers={
                **headers,
                "Idempotency-Key": "shared-budget-emergency",
            },
            json={"reason": "fixture shared spending denial"},
        )
        assert safety.status_code == 200 and safety.json()["safety_state"] == "NO_NEW_RISK"
        assert client.get("/api/v1/orders").json()["orders"] == []
        status = client.get("/api/v1/decisions/provider").json()
        assert "fixture-key" not in str(status) and str(tmp_path) not in str(status)
    assert len(calls) == (1 if cause == "zero_aggregate_budget" else 0)

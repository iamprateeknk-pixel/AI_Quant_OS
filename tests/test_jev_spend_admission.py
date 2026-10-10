"""Jev input-only accounting under the published pinned-model context ceiling."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from ai_quant_os.agent_permissions import ROLE_CEILINGS
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.jev_adapter import JevClassifier, jev_from_environment
from ai_quant_os.jev_spend_policy import JEV_INPUT_CEILING, JevInputSpendPolicy
from ai_quant_os.provider_call_admission import (
    AdmittedClassifier,
    ProviderCallAdmission,
    ProviderOwner,
)
from ai_quant_os.agent_call_budgets import AgentCallBudget
from ai_quant_os.provider_spend_policy import SpendRule
from ai_quant_os.provider_spend_store import ProviderSpendStore
from test_jev_adapter import MODEL, NOW, response, world


def policy(tokens=200000, money=10000000, reserve=0):
    return JevInputSpendPolicy(
        model=MODEL,
        input_nano_usd=42,
        output_nano_usd=0,
        rate_reference="https://docs.typesafe.ai/models observed 2026-10-10",
        rules=tuple(
            SpendRule(role, tokens, tokens, reserve, reserve, money, money, 0, 0)
            for role in ROLE_CEILINGS
        ),
    )


def reserve(store, *, now=NOW, version="0.2.0"):
    return store.reserve(
        agent_id="A02",
        agent_version=version,
        model=MODEL,
        request_sha256="a" * 64,
        input_tokens=JEV_INPUT_CEILING,
        max_output_tokens=0,
        now=now,
    )


def counters(store, now=NOW):
    return next(row for row in store.status(now)["agents"] if row["agent_id"] == "A02")


def classifier(store, handler, **options):
    return JevClassifier(
        api_key="fixture-only-key",
        model=MODEL,
        spend_store=store,
        spend_owner=ProviderOwner("A02", "0.2.0"),
        utc_clock=lambda: NOW,
        transport=httpx.MockTransport(handler),
        **options,
    )


def test_pinned_input_policy_is_canonical_and_rejects_unproved_contracts():
    original = policy()
    assert JevInputSpendPolicy.decode(original.encode()) == original
    for changes in (
        {"model": "jev-latest"},
        {"model": "jev-1.14.0"},
        {"output_nano_usd": 1},
        {"input_nano_usd": 41},
        {"input_nano_usd": True},
        {"rules": original.rules[:-1]},
    ):
        with pytest.raises(ValueError):
            replace(original, **changes)
    with pytest.raises(ValueError):
        JevInputSpendPolicy.decode(original.encode().replace(b'"model":', b'"model":"x","model":'))


def test_additive_policy_schema_matches_native_policy_and_keeps_openai_distinct():
    from jsonschema import Draft202012Validator
    from ai_quant_os.provider_spend_policy import ProviderSpendPolicy

    root = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (root / "docs/schemas/jev-input-spend-policy.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    value = json.loads(policy().encode())
    validator = Draft202012Validator(schema)
    validator.validate(value)
    invalid = {**value, "rules": [value["rules"][0]] * 18}
    assert not validator.is_valid(invalid)
    with pytest.raises(ValueError):
        JevInputSpendPolicy.decode(json.dumps(invalid).encode())
    with pytest.raises(ValueError):
        ProviderSpendPolicy.decode(policy().encode())
    for field, wrong in (("input_reservation_ceiling", 65536.0), ("input_nano_usd", True)):
        with pytest.raises(ValueError):
            JevInputSpendPolicy.decode(json.dumps({**value, field: wrong}).encode())


def test_full_input_hold_survives_restart_and_settles_free_outputs(tmp_path):
    path = tmp_path / "jev.db"
    store = ProviderSpendStore(path, policy())
    identity = reserve(store)
    assert counters(store)["daily_tokens"] == JEV_INPUT_CEILING
    assert counters(store)["daily_nano_usd"] == JEV_INPUT_CEILING * 42
    reopened = ProviderSpendStore(path, None)
    assert reopened.status(NOW)["accounting"] == "JEV_INPUT_ONLY_AT_DECLARED_RATE_CEILING"
    reopened.settle(identity, input_tokens=100, output_tokens=99999, now=NOW)
    assert counters(reopened)["daily_tokens"] == 100
    assert counters(reopened)["daily_nano_usd"] == 4200
    assert counters(reopened)["pending_count"] == 0
    reopened.settle(identity, input_tokens=100, output_tokens=99999, now=NOW)
    with pytest.raises(ValueError):
        reopened.settle(identity, input_tokens=100, output_tokens=99998, now=NOW)


@pytest.mark.parametrize("limit", ["token", "money", "reserve"])
def test_shared_role_limits_reject_second_version_without_spending_protected_capacity(
    tmp_path, limit
):
    settings = (
        {"tokens": JEV_INPUT_CEILING}
        if limit == "token"
        else {"money": JEV_INPUT_CEILING * 42}
        if limit == "money"
        else {"tokens": JEV_INPUT_CEILING + 100, "reserve": 100}
    )
    store = ProviderSpendStore(tmp_path / "jev.db", policy(**settings))
    reserve(store)
    with pytest.raises(PermissionError):
        reserve(store, version="0.1.0")
    later = NOW + timedelta(days=40)
    with pytest.raises(PermissionError):
        reserve(ProviderSpendStore(store.path, None), now=later)
    assert counters(store, later)["unsettled_tokens"] == JEV_INPUT_CEILING


def test_concurrent_original_ledger_admits_exactly_one_full_context(tmp_path):
    path = tmp_path / "jev.db"
    ProviderSpendStore(path, policy(tokens=JEV_INPUT_CEILING))

    def attempt(_):
        try:
            reserve(ProviderSpendStore(path, None))
            return True
        except PermissionError:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(attempt, range(8))) == 1


def test_contract_ceiling_cannot_be_lowered_by_a_caller_and_breach_is_retained(tmp_path):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    for inputs, outputs in [(100, 0), (JEV_INPUT_CEILING, 128)]:
        with pytest.raises(ValueError):
            store.reserve(
                agent_id="A02",
                agent_version="0.2.0",
                model=MODEL,
                request_sha256="a" * 64,
                input_tokens=inputs,
                max_output_tokens=outputs,
                now=NOW,
            )
    identity = reserve(store)
    with pytest.raises(PermissionError):
        store.settle(identity, input_tokens=JEV_INPUT_CEILING + 1, output_tokens=1, now=NOW)
    reopened = ProviderSpendStore(store.path, None)
    assert reopened.status(NOW)["state"] == "BREACHED"
    assert counters(reopened)["daily_tokens"] == JEV_INPUT_CEILING + 1
    with pytest.raises(PermissionError):
        reserve(reopened)


def test_adapter_reserves_before_single_official_request_and_reports_input_semantics(tmp_path):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert counters(store)["unsettled_tokens"] == JEV_INPUT_CEILING
        body = json.loads(request.content)
        assert set(body) == {"model", "state", "questions"}
        assert "regime" not in body["state"]["instrument"]
        return httpx.Response(
            200, json=response(usage={"input_tokens": 123, "output_tokens": 99999})
        )

    adapter = classifier(store, handle)
    candidate = asyncio.run(adapter.classify(world(), "EURUSD"))
    assert candidate.classification == "RANGING"
    assert len(calls) == 1
    assert counters(store)["daily_tokens"] == 123
    assert counters(store)["daily_nano_usd"] == 5166
    status = adapter.status()["spending"]
    assert status["state"] == "ENFORCED"
    assert status["input_token_budget_enforced"] is True
    assert status["total_token_budget_enforced"] is False
    assert status["invoice_verified"] is False


@pytest.mark.parametrize(
    "change", ["wrong_model", "invalid_answer", "missing_usage", "oversized_usage"]
)
def test_rejected_content_is_charged_only_from_bounded_matching_native_usage(tmp_path, change):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    payload = response()
    if change == "wrong_model":
        payload["model"] = "jev-1.14.0"
    elif change == "invalid_answer":
        payload["answers"] = {"regime": {"type": "noul", "noul": 0.9}}
    elif change == "missing_usage":
        payload.pop("usage")
    else:
        payload["usage"]["input_tokens"] = 10**9 + 1
    adapter = classifier(store, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ValueError):
        asyncio.run(adapter.classify(world(), "EURUSD"))
    assert counters(store)["pending_count"] == (0 if change == "invalid_answer" else 1)
    assert counters(store)["daily_tokens"] == (
        100 if change == "invalid_answer" else JEV_INPUT_CEILING
    )


def test_cancellation_retains_original_hold_without_retry(tmp_path):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    entered = asyncio.Event()
    calls = []

    async def handle(request):
        calls.append(request)
        entered.set()
        await asyncio.Future()

    adapter = classifier(store, handle)

    async def invoke():
        task = asyncio.create_task(adapter.classify(world(), "EURUSD"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(invoke())
    assert len(calls) == 1
    assert counters(ProviderSpendStore(store.path, None))["unsettled_tokens"] == JEV_INPUT_CEILING


def test_guard_rechecks_after_durable_reservation_before_provider_effect(tmp_path):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    calls = []

    def guard():
        if counters(store)["pending_count"]:
            raise PermissionError("fixture permission revoked")

    adapter = classifier(store, lambda request: calls.append(request))
    with pytest.raises(ValueError):
        asyncio.run(adapter.classify_with_admission(world(), "EURUSD", guard))
    assert calls == []
    assert counters(store)["pending_count"] == 1


def test_expired_source_is_denied_before_hold_and_transport(tmp_path):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    calls = []
    adapter = classifier(store, lambda request: calls.append(request))
    stale = replace(world(), valid_until=NOW)
    with pytest.raises(ValueError):
        asyncio.run(adapter.classify(stale, "EURUSD"))
    assert calls == []
    assert counters(store)["pending_count"] == 0


def test_environment_requires_original_ledger_and_declared_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_QUANT_JEV_ENABLED", "true")
    monkeypatch.setenv("AI_QUANT_JEV_API_KEY", "fixture-only-key")
    monkeypatch.setenv("AI_QUANT_JEV_MODEL", MODEL)
    monkeypatch.delenv("AI_QUANT_JEV_SPEND_PATH", raising=False)
    with pytest.raises(ValueError, match="persistent spending"):
        jev_from_environment()
    policy_path = tmp_path / "policy.json"
    policy_path.write_bytes(policy().encode())
    monkeypatch.setenv("AI_QUANT_JEV_SPEND_PATH", str(tmp_path / "jev.db"))
    monkeypatch.setenv("AI_QUANT_JEV_SPEND_POLICY", str(policy_path))
    monkeypatch.delenv("AI_QUANT_CLASSIFY_OWNER", raising=False)
    with pytest.raises(ValueError, match="owner"):
        jev_from_environment()
    monkeypatch.setenv("AI_QUANT_CLASSIFY_OWNER", "A02@0.2.0")
    first = jev_from_environment()
    monkeypatch.delenv("AI_QUANT_JEV_SPEND_POLICY")
    second = jev_from_environment()
    assert first.provider_version == second.provider_version == MODEL
    assert first.spend_owner == second.spend_owner == ProviderOwner("A02", "0.2.0")
    assert "fixture-only-key" not in repr(second) + json.dumps(second.status())


@pytest.mark.parametrize("change", ["expiry", "quarantine"])
def test_actual_admitted_route_rechecks_permission_and_expiry_after_spend_reservation(
    tmp_path, change
):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    registry = load_agent_catalog()
    current = [NOW]
    original = store.reserve
    calls = []

    def changed(**values):
        identity = original(**values)
        if change == "expiry":
            current[0] = world().valid_until
        else:
            registry.set_lifecycle("A02", "0.2.0", "QUARANTINED")
        return identity

    store.reserve = changed
    adapter = classifier(store, lambda request: calls.append(request))
    admission = ProviderCallAdmission(
        registry,
        AgentCallBudget(None, policy=None, clock=lambda: current[0]),
        ProviderOwner("A02", "0.2.0"),
        clock=lambda: current[0],
    )
    with pytest.raises(ValueError):
        asyncio.run(AdmittedClassifier(adapter, admission).classify(world(), "EURUSD"))
    assert calls == []
    assert counters(store)["pending_count"] == 1


def test_different_route_owner_cannot_spend_another_roles_ledger(tmp_path):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    adapter = classifier(store, lambda request: httpx.Response(200, json=response()))
    admission = ProviderCallAdmission(
        load_agent_catalog(),
        AgentCallBudget(None, policy=None, clock=lambda: NOW),
        ProviderOwner("A03", "0.2.0"),
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="owner"):
        AdmittedClassifier(adapter, admission)
    assert counters(store)["pending_count"] == 0


def test_native_above_context_usage_is_retained_and_stops_future_dispatch(tmp_path):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json=response(
                usage={
                    "input_tokens": JEV_INPUT_CEILING + 1,
                    "output_tokens": 12,
                }
            ),
        )

    adapter = classifier(store, handle)
    for _ in range(2):
        with pytest.raises(ValueError):
            asyncio.run(adapter.classify(world(), "EURUSD"))
    assert len(calls) == 1
    assert store.status(NOW)["state"] == "BREACHED"
    assert counters(store)["daily_tokens"] == JEV_INPUT_CEILING + 1
    assert counters(store)["pending_count"] == 0


def test_duplicate_native_usage_keeps_full_hold(tmp_path):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    raw = json.dumps(response()).replace(
        '"input_tokens": 100', '"input_tokens": 100, "input_tokens": 1'
    )
    adapter = classifier(store, lambda request: httpx.Response(200, content=raw))
    with pytest.raises(ValueError):
        asyncio.run(adapter.classify(world(), "EURUSD"))
    assert counters(store)["unsettled_tokens"] == JEV_INPUT_CEILING


def test_permission_revoked_during_response_rejects_candidate_but_charges_usage(tmp_path):
    store = ProviderSpendStore(tmp_path / "jev.db", policy())
    registry = load_agent_catalog()

    def handle(request):
        registry.set_lifecycle("A02", "0.2.0", "QUARANTINED")
        return httpx.Response(200, json=response())

    adapter = classifier(store, handle)
    admission = ProviderCallAdmission(
        registry,
        AgentCallBudget(None, policy=None, clock=lambda: NOW),
        ProviderOwner("A02", "0.2.0"),
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError):
        asyncio.run(AdmittedClassifier(adapter, admission).classify(world(), "EURUSD"))
    assert counters(store)["pending_count"] == 0
    assert counters(store)["daily_tokens"] == 100


@pytest.mark.parametrize("cause", ["zero_budget", "corrupt_pin"])
def test_actual_api_spending_failure_keeps_quantitative_fallback_and_emergency_controls(
    tmp_path, cause
):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app

    store = ProviderSpendStore(
        tmp_path / "jev.db", policy(tokens=0 if cause == "zero_budget" else 200000)
    )
    calls = []
    adapter = classifier(store, lambda request: calls.append(request))
    if cause == "corrupt_pin":
        store.pin.write_bytes(b"corrupt")
    with TestClient(
        create_app(
            operator_token="fixture-token",
            paper_db_path=tmp_path / "paper.db",
            clock=lambda: NOW,
            fast_classifier=adapter,
            classification_owner=ProviderOwner("A02", "0.2.0"),
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
        classified = client.post(
            "/api/v1/paper/decisions/classify",
            headers=headers,
            json={
                "instrument_id": "EURUSD",
                "task_id": "fixture",
            },
        )
        assert classified.status_code == 200
        assert classified.json()["model_route"] == "QUANTITATIVE"
        assert (
            client.post(
                "/api/v1/control/no-new-risk",
                headers={
                    **headers,
                    "Idempotency-Key": "jev-budget-emergency",
                },
                json={"reason": "fixture provider spending unavailable"},
            ).json()["safety_state"]
            == "NO_NEW_RISK"
        )
    assert calls == []

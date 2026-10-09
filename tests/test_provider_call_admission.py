import asyncio
import importlib

import pytest

from ai_quant_os.agent_call_budgets import AgentCallBudget, AgentCallPolicy, AgentCallRule
from ai_quant_os.agent_registry import load_agent_catalog
from test_decision_gateway import NOW, world


def module():
    assert importlib.util.find_spec("ai_quant_os.provider_call_admission"), (
        "Provider ownership admission is missing"
    )
    return importlib.import_module("ai_quant_os.provider_call_admission")


def guard(path, calls=1):
    policy = AgentCallPolicy(
        tuple(AgentCallRule(f"A{i:02}", calls, 10, 0, 0) for i in range(1, 19))
    )
    return AgentCallBudget(path, policy=policy, clock=lambda: NOW)


class Classifier:
    provider_id = "TRUSTED_TEST"
    provider_version = "fixed-1"

    def __init__(self):
        self.calls = 0

    async def classify(self, snapshot, instrument):
        self.calls += 1
        raise RuntimeError("private provider failure")


def test_failed_provider_attempt_is_reserved_and_versions_share_durable_quota(tmp_path):
    admission = module()
    path = tmp_path / "calls.sqlite3"
    budget = guard(path)
    provider = Classifier()
    owner = admission.ProviderOwner("A02", "0.2.0")
    wrapper = admission.AdmittedClassifier(
        provider,
        admission.ProviderCallAdmission(load_agent_catalog(), budget, owner, clock=lambda: NOW),
    )
    with pytest.raises(RuntimeError):
        asyncio.run(wrapper.classify(world(), "EURUSD"))
    restored = AgentCallBudget(path, policy=None, clock=lambda: NOW)
    older = admission.AdmittedClassifier(
        provider,
        admission.ProviderCallAdmission(
            load_agent_catalog(),
            restored,
            admission.ProviderOwner("A02", "0.1.0"),
            clock=lambda: NOW,
        ),
    )
    with pytest.raises(PermissionError):
        asyncio.run(older.classify(world(), "EURUSD"))
    assert provider.calls == 1
    assert restored.status()["agents"][1]["daily_calls"] == 1


def test_retained_quota_requires_declared_owner_and_never_dispatches_unowned_provider(tmp_path):
    admission = module()
    provider = Classifier()
    budget = guard(tmp_path / "calls.sqlite3")
    wrapper = admission.AdmittedClassifier(
        provider,
        admission.ProviderCallAdmission(load_agent_catalog(), budget, None, clock=lambda: NOW),
    )
    with pytest.raises(PermissionError):
        asyncio.run(wrapper.classify(world(), "EURUSD"))
    assert provider.calls == 0
    assert budget.status()["agents"][1]["daily_calls"] == 0


def test_quarantine_denies_before_charge_and_owner_role_requires_model_capability(tmp_path):
    admission = module()
    registry = load_agent_catalog()
    budget = guard(tmp_path / "calls.sqlite3", 0)
    provider = Classifier()
    owner = admission.ProviderOwner("A02", "0.2.0")
    registry.set_lifecycle("A02", "0.2.0", "QUARANTINED")
    wrapper = admission.AdmittedClassifier(
        provider, admission.ProviderCallAdmission(registry, budget, owner, clock=lambda: NOW)
    )
    with pytest.raises(PermissionError):
        asyncio.run(wrapper.classify(world(), "EURUSD"))
    assert provider.calls == 0
    assert budget.status()["agents"][1]["daily_calls"] == 0
    with pytest.raises(ValueError):
        admission.ProviderOwner("A18", "0.2.0")


def test_api_zero_quota_blocks_both_mock_transports_and_keeps_paper_control(tmp_path):
    import httpx
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app
    from ai_quant_os.jev_adapter import JevClassifier
    from ai_quant_os.openai_reviewer import OpenAIReviewer
    from test_jev_adapter import MODEL as JEV_MODEL
    from test_openai_reviewer import MODEL as REVIEW_MODEL

    admission = module()
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500))
    policy = AgentCallPolicy(tuple(AgentCallRule(f"A{i:02}", 0, 10, 0, 0) for i in range(1, 19)))
    app = create_app(
        operator_token="fixture-token",
        paper_db_path=tmp_path / "paper.sqlite3",
        clock=lambda: NOW,
        agent_call_policy=policy,
        fast_classifier=JevClassifier(api_key="fixture-key", model=JEV_MODEL, transport=transport),
        deep_reviewer=OpenAIReviewer(
            api_key="fixture-key", model=REVIEW_MODEL, transport=transport
        ),
        classification_owner=admission.ProviderOwner("A02", "0.2.0"),
        review_owner=admission.ProviderOwner("A03", "0.2.0"),
    )
    with TestClient(app) as client:
        headers = {"X-Operator-Token": "fixture-token"}
        quote = client.post(
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
        assert quote.status_code == 200
        result = client.post(
            "/api/v1/paper/decisions/classify",
            headers=headers,
            json={"instrument_id": "EURUSD", "task_id": "fixture"},
        )
        assert result.status_code == 200
        assert result.json()["model_route"] == "QUANTITATIVE"
        status = client.get("/api/v1/decisions/provider").json()
        assert status["classification_call_admission"]["state"] == "ENFORCED"
        assert status["review_call_admission"]["owner"] == "A03@0.2.0"
        assert "fixture-key" not in str(status)
        assert client.get("/api/v1/system/status").status_code == 200
        # Corruption is detected without making emergency control depend on model admission.
        (tmp_path / "paper.agent-calls.sqlite3.pin.json").write_bytes(b"corrupt")
        assert (
            client.get("/api/v1/decisions/provider").json()["classification_call_admission"][
                "state"
            ]
            == "UNAVAILABLE"
        )
        emergency = client.post(
            "/api/v1/control/no-new-risk",
            headers={**headers, "Idempotency-Key": "provider-budget-emergency"},
            json={"reason": "provider quota fixture"},
        )
        assert emergency.status_code == 200
        assert emergency.json()["safety_state"] == "NO_NEW_RISK"
    assert calls == []


def test_classification_and_review_concurrently_share_last_call(tmp_path):
    from ai_quant_os.decision_gateway import DecisionGateway

    admission = module()
    budget = guard(tmp_path / "calls.sqlite3")
    source = world()
    decision = asyncio.run(
        DecisionGateway(clock=lambda: NOW).classify(
            source, instrument_id="EURUSD", task_id="shared"
        )
    )
    calls = []

    class Both:
        provider_id = "TRUSTED_TEST"
        provider_version = "fixed-1"

        async def classify(self, snapshot, instrument):
            calls.append("classification")
            return "fixture"

        async def review(self, snapshot, parent):
            calls.append("review")
            return "fixture"

    provider = Both()
    quota = admission.ProviderCallAdmission(
        load_agent_catalog(), budget, admission.ProviderOwner("A02", "0.2.0"), clock=lambda: NOW
    )

    async def invoke():
        return await asyncio.gather(
            admission.AdmittedClassifier(provider, quota).classify(source, "EURUSD"),
            admission.AdmittedReviewer(provider, quota).review(source, decision),
            return_exceptions=True,
        )

    results = asyncio.run(invoke())
    assert len(calls) == 1
    assert sum(isinstance(row, PermissionError) for row in results) == 1
    assert budget.status()["agents"][1]["daily_calls"] == 1


def test_direct_mock_reviewer_zero_quota_never_reaches_transport(tmp_path):
    import httpx
    from ai_quant_os.openai_reviewer import OpenAIReviewer
    from test_openai_reviewer import MODEL, inputs

    admission = module()
    calls = []
    provider = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)),
    )
    quota = admission.ProviderCallAdmission(
        load_agent_catalog(),
        guard(tmp_path / "calls.sqlite3", 0),
        admission.ProviderOwner("A03", "0.2.0"),
        clock=lambda: NOW,
    )
    source, decision = asyncio.run(inputs())
    with pytest.raises(PermissionError):
        asyncio.run(admission.AdmittedReviewer(provider, quota).review(source, decision))
    assert calls == []


@pytest.mark.parametrize("change", ["expiry", "quarantine"])
def test_admission_rechecks_source_and_permission_after_durable_charge(tmp_path, change):
    from datetime import timedelta

    admission = module()
    registry = load_agent_catalog()
    budget = guard(tmp_path / "calls.sqlite3")
    current = [NOW]
    reserve = budget.reserve

    def changed(invocation):
        reserve(invocation)
        if change == "expiry":
            current[0] += timedelta(seconds=10)
        else:
            registry.set_lifecycle("A02", "0.2.0", "QUARANTINED")

    budget.reserve = changed
    provider = Classifier()
    quota = admission.ProviderCallAdmission(
        registry, budget, admission.ProviderOwner("A02", "0.2.0"), clock=lambda: current[0]
    )
    with pytest.raises((PermissionError, ValueError)):
        asyncio.run(admission.AdmittedClassifier(provider, quota).classify(world(), "EURUSD"))
    assert provider.calls == 0
    assert budget.status()["agents"][1]["daily_calls"] == 1


def test_cancelled_admission_withholds_provider_but_retains_committed_attempt(tmp_path):
    from threading import Event

    admission = module()
    budget = guard(tmp_path / "calls.sqlite3")
    entered, release = Event(), Event()
    reserve = budget.reserve

    def slow(invocation):
        reserve(invocation)
        entered.set()
        assert release.wait(5)

    budget.reserve = slow
    provider = Classifier()
    quota = admission.ProviderCallAdmission(
        load_agent_catalog(), budget, admission.ProviderOwner("A02", "0.2.0"), clock=lambda: NOW
    )

    async def invoke():
        task = asyncio.create_task(
            admission.AdmittedClassifier(provider, quota).classify(world(), "EURUSD")
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()

    asyncio.run(invoke())
    assert provider.calls == 0
    assert budget.status()["agents"][1]["daily_calls"] == 1


@pytest.mark.parametrize("raw", ["A18@0.2.0", "A02@00.2.0", "A02@0.2.0@other", "A02@0.99.0"])
def test_invalid_static_owner_configuration_is_rejected(monkeypatch, raw):
    from ai_quant_os.api import create_app

    monkeypatch.setenv("AI_QUANT_CLASSIFICATION_OWNER", raw)
    with pytest.raises((ValueError, KeyError)):
        create_app(clock=lambda: NOW)


@pytest.mark.parametrize("archive", ["retained", "corrupt"])
def test_removed_policy_cannot_bypass_provider_admission(tmp_path, archive):
    admission = module()
    path = tmp_path / "calls.sqlite3"
    guard(path)
    if archive == "corrupt":
        path.with_suffix(path.suffix + ".pin.json").write_bytes(b"corrupt")
    restored = AgentCallBudget(path, policy=None, clock=lambda: NOW)
    provider = Classifier()
    quota = admission.ProviderCallAdmission(load_agent_catalog(), restored, None, clock=lambda: NOW)
    with pytest.raises(PermissionError):
        asyncio.run(admission.AdmittedClassifier(provider, quota).classify(world(), "EURUSD"))
    assert provider.calls == 0
    assert quota.status()["state"] == ("UNAVAILABLE" if archive == "corrupt" else "OWNER_REQUIRED")


def test_disabled_budget_preserves_legacy_unowned_provider_behavior():
    admission = module()
    provider = Classifier()
    quota = admission.ProviderCallAdmission(
        load_agent_catalog(),
        AgentCallBudget(None, policy=None, clock=lambda: NOW),
        None,
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeError):
        asyncio.run(admission.AdmittedClassifier(provider, quota).classify(world(), "EURUSD"))
    assert provider.calls == 1
    assert quota.status() == {
        "state": "BUDGET_NOT_CONFIGURED",
        "owner": None,
        "token_budget_enforced": False,
        "monetary_budget_enforced": False,
    }

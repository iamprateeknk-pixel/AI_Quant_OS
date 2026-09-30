import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.decision_gateway import DecisionGateway
from ai_quant_os.deep_review import DeepReviewDispatcher
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def world():
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    engine.record_bars(
        tuple(
            Bar("EURUSD", NOW - timedelta(seconds=i), *(Decimal("1.1"),) * 4, Decimal(1))
            for i in range(3)
        ),
        now=NOW,
    )
    return engine.snapshot(
        (MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.1001"), NOW, "fixture", True),),
        now=NOW,
    )


class Reviewer:
    provider_id = "FIXTURE_REVIEW"
    provider_version = "1"

    def __init__(self, changes=None, fail=False, slow=False):
        self.changes = changes or {}
        self.fail = fail
        self.slow = slow
        self.calls = 0
        self.cancelled = False

    async def review(self, snapshot, decision):
        self.calls += 1
        if self.fail:
            raise RuntimeError("private-key-details")
        if self.slow:
            try:
                await asyncio.sleep(10)
            finally:
                self.cancelled = True
        return {
            "schema_version": "1.0",
            "world_state_version": snapshot.world_state_version,
            "instrument_id": decision.output.instrument_id,
            "parent_trace_id": decision.trace_id,
            "classification": "TRENDING",
            "confidence": "0.95",
            "evidence_refs": decision.evidence_refs,
            "contradictions": [],
            "rationale": "Fixture review of supplied features.",
            "created_at": NOW,
            "expires_at": NOW + timedelta(seconds=3),
            **self.changes,
        }


def run(reviewer=None, **kwargs):
    async def invoke():
        snapshot = world()
        decision = await DecisionGateway(clock=lambda: NOW).classify(
            snapshot, instrument_id="EURUSD", task_id="fixture"
        )
        return await DeepReviewDispatcher(reviewer=reviewer, clock=lambda: NOW, **kwargs).dispatch(
            snapshot, decision
        )

    return asyncio.run(invoke())


def test_completed_review_retains_parent_evidence_expiry_and_escalation():
    result = run(Reviewer())
    assert result.output.status == "COMPLETED"
    assert result.output.classification == "TRENDING"
    assert result.output.rationale
    assert result.requires_escalation is True
    assert result.expires_at == NOW + timedelta(seconds=3)
    assert result.model_route == "FIXTURE_REVIEW"
    assert result.output.parent_trace_id in result.input_refs
    assert result.contradictions == ("FAST_REGIME:RANGING",)
    assert result.confidence == Decimal("0.95")


def test_unconfigured_and_failed_review_keep_review_pending_without_private_errors():
    assert run().output.status == "NOT_CONFIGURED"
    failed = run(Reviewer(fail=True))
    assert failed.output.status == "FAILED"
    assert failed.output.classification is None
    assert failed.requires_escalation
    assert "private-key-details" not in failed.model_dump_json()


@pytest.mark.parametrize(
    "change",
    [
        {"world_state_version": "wrong"},
        {"instrument_id": "GBPUSD"},
        {"parent_trace_id": "wrong"},
        {"evidence_refs": ["invented"]},
        {"evidence_refs": []},
        {"confidence": "NaN"},
        {"classification": "BUY"},
        {"approved_exposure": "1000"},
        {"created_at": NOW + timedelta(seconds=1)},
        {"expires_at": NOW},
    ],
)
def test_invalid_review_is_withheld(change):
    result = run(Reviewer(change))
    assert result.output.status == "INVALID"
    assert result.output.classification is None
    assert result.output.rationale is None
    assert result.confidence == 0


def test_timeout_cancels_provider_and_preserves_pending_review():
    reviewer = Reviewer(slow=True)
    assert run(reviewer, timeout_seconds=0.01).output.status == "TIMEOUT"
    assert reviewer.cancelled


def test_wait_for_data_and_expired_or_mismatched_parent_never_dispatch():
    async def invoke():
        snapshot = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
        decision = await DecisionGateway(clock=lambda: NOW).classify(
            snapshot, instrument_id="EURUSD", task_id="fixture"
        )
        reviewer = Reviewer()
        dispatcher = DeepReviewDispatcher(reviewer=reviewer, clock=lambda: NOW)
        assert (await dispatcher.dispatch(snapshot, decision)).output.status == "WAIT_FOR_DATA"
        assert reviewer.calls == 0
        with pytest.raises(ValueError):
            await dispatcher.dispatch(
                snapshot, decision.model_copy(update={"world_state_version": "wrong"})
            )
        with pytest.raises(ValueError):
            await DeepReviewDispatcher(
                reviewer=reviewer, clock=lambda: NOW + timedelta(seconds=6)
            ).dispatch(snapshot, decision)
        assert reviewer.calls == 0

    asyncio.run(invoke())


def test_concurrency_limit_is_immediate_and_releases_after_timeout():
    async def invoke():
        snapshot = world()
        decision = await DecisionGateway(clock=lambda: NOW).classify(
            snapshot, instrument_id="EURUSD", task_id="fixture"
        )
        reviewer = Reviewer(slow=True)
        dispatcher = DeepReviewDispatcher(
            reviewer=reviewer, clock=lambda: NOW, max_concurrency=1, timeout_seconds=0.01
        )
        results = await asyncio.gather(*(dispatcher.dispatch(snapshot, decision) for _ in range(5)))
        assert sorted(result.output.status for result in results) == ["BUSY"] * 4 + ["TIMEOUT"]
        assert reviewer.calls == 1
        assert (await dispatcher.dispatch(snapshot, decision)).output.status == "TIMEOUT"
        assert reviewer.calls == 2

    asyncio.run(invoke())


def test_api_links_review_trace_and_never_creates_orders():
    client = TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, deep_reviewer=Reviewer())
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
    response = client.post(
        "/api/v1/paper/decisions/classify",
        headers=headers,
        json={"instrument_id": "EURUSD", "task_id": "fixture"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deep_review"]["output"]["status"] == "COMPLETED"
    assert body["deep_review"]["output"]["parent_trace_id"] == body["trace_id"]
    events = client.get("/api/v1/audit").json()["events"]
    assert events[-1]["deep_review_trace_id"] == body["deep_review"]["trace_id"]
    assert client.get("/api/v1/orders").json()["orders"] == []


def test_api_rejects_review_expiring_during_audit_even_when_fast_decision_is_current(monkeypatch):
    from ai_quant_os.paper_state_store import PaperStateStore

    time = [NOW]
    original = PaperStateStore.record_event

    def delayed(self, key, event):
        original(self, key, event)
        if event["action"] == "PAPER_CLASSIFICATION":
            time[0] = NOW + timedelta(seconds=4)

    monkeypatch.setattr(PaperStateStore, "record_event", delayed)

    class FastProvider:
        provider_id = "FAST_FIXTURE"
        provider_version = "1"

        async def classify(self, snapshot, instrument_id):
            return {
                "schema_version": "1.0",
                "world_state_version": snapshot.world_state_version,
                "instrument_id": instrument_id,
                "classification": "RANGING",
                "confidence": "0.9",
                "created_at": NOW,
                "expires_at": NOW + timedelta(seconds=5),
                "evidence_refs": snapshot.instruments[0].evidence_refs,
            }

    client = TestClient(
        create_app(
            operator_token="fixture-token",
            clock=lambda: time[0],
            fast_classifier=FastProvider(),
            deep_reviewer=Reviewer(),
        )
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
    response = client.post(
        "/api/v1/paper/decisions/classify",
        headers=headers,
        json={"instrument_id": "EURUSD", "task_id": "fixture"},
    )
    assert response.status_code == 409
    assert "expired" in response.json()["detail"]


def test_caller_cancellation_releases_admission_and_is_not_swallowed():
    async def invoke():
        snapshot = world()
        decision = await DecisionGateway(clock=lambda: NOW).classify(
            snapshot, instrument_id="EURUSD", task_id="fixture"
        )
        reviewer = Reviewer(slow=True)
        dispatcher = DeepReviewDispatcher(
            reviewer=reviewer, clock=lambda: NOW, max_concurrency=1, timeout_seconds=0.01
        )
        task = asyncio.create_task(dispatcher.dispatch(snapshot, decision))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await dispatcher.dispatch(snapshot, decision)).output.status == "TIMEOUT"

    asyncio.run(invoke())


def test_parent_expiring_while_review_runs_rejects_result():
    time = [NOW]

    class AdvancingReviewer(Reviewer):
        async def review(self, snapshot, decision):
            response = await super().review(snapshot, decision)
            time[0] = NOW + timedelta(seconds=6)
            return response

    async def invoke():
        snapshot = world()
        decision = await DecisionGateway(clock=lambda: NOW).classify(
            snapshot, instrument_id="EURUSD", task_id="fixture"
        )
        with pytest.raises(ValueError, match="expired"):
            await DeepReviewDispatcher(
                reviewer=AdvancingReviewer(), clock=lambda: time[0]
            ).dispatch(snapshot, decision)

    asyncio.run(invoke())

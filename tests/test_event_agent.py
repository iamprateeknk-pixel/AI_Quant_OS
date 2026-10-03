from datetime import datetime, timedelta, timezone
import json
from decimal import Decimal
import pytest
import sqlite3

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from ai_quant_os.api import create_app

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-events"}


def publish(client):
    body = {
        "observed_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(seconds=120)).isoformat(),
        "coverage_start": (NOW - timedelta(hours=1)).isoformat(),
        "coverage_end": (NOW + timedelta(days=1)).isoformat(),
        "economic_calendar": [
            {
                "event_id": "payrolls",
                "kind": "SCHEDULED",
                "currencies": ["USD"],
                "severity": "HIGH",
                "occurs_at": (NOW + timedelta(minutes=5)).isoformat(),
                "reported_at": NOW.isoformat(),
            }
        ],
        "event_classifications": [],
    }
    response = client.post("/api/v1/paper/events/feed", headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def execute(client, run_id="event-1"):
    response = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={"agent_id": "A06", "agent_version": "0.2.0", "task_id": "event-task"},
    )
    assert response.status_code == 200, response.text
    context = response.json()
    response = client.post(
        "/api/v1/paper/agents/execute",
        headers=HEADERS,
        json={"run_id": run_id, "context_ref": context["context_ref"]},
    )
    assert response.status_code == 200, response.text
    return context, response.json()["receipt"]


def test_actual_graph_measures_direct_currency_event_risk_and_retains_restart(tmp_path):
    args = dict(
        operator_token="fixture-events", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
    )
    with TestClient(create_app(**args)) as client:
        feed = publish(client)
        context, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"
        output = json.loads(receipt["output_json"])
        assert output["feed_ref"] == feed["feed_ref"]
        assert datetime.fromisoformat(output["observation_as_of"]) == NOW
        assert datetime.fromisoformat(output["feed_observed_at"]) == NOW
        assert output["input_refs"] == output["evidence_refs"] == context["input_refs"]
        rows = {row["instrument_id"]: row for row in output["assessments"]}
        assert rows["EURUSD"]["blackout_recommended"] is True
        assert rows["EURJPY"]["blackout_recommended"] is False
        assert [
            output["measurements"][index]["event_id"] for index in rows["EURUSD"]["event_indices"]
        ] == ["payrolls"]
        assert all(row["may_add_risk"] is False for row in rows.values())
        assert output["confidence"] == "0" and output["requires_escalation"] is True
        from ai_quant_os.event_agent import EventRiskState

        Draft202012Validator(EventRiskState.model_json_schema()).validate(output)
    with TestClient(create_app(**args)) as client:
        assert (
            client.get("/api/v1/agents/graph-runs/event-1", headers=HEADERS).json()["receipt"]
            == receipt
        )
        assert client.get("/api/v1/events/feed").json()["feed_ref"] == feed["feed_ref"]


def test_missing_and_expired_feeds_never_issue_a_clear_recommendation(tmp_path):
    time = [NOW]
    with TestClient(
        create_app(
            operator_token="fixture-events",
            clock=lambda: time[0],
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        _, missing = execute(client, "missing")
        missing = json.loads(missing["output_json"])
        assert missing["feed_status"] == "MISSING"
        assert all(row["blackout_recommended"] is None for row in missing["assessments"])
        publish(client)
        time[0] = NOW + timedelta(seconds=121)
        _, expired = execute(client, "expired")
        expired = json.loads(expired["output_json"])
        assert expired["feed_status"] == "STALE"
        assert all(row["blackout_recommended"] is None for row in expired["assessments"])


def test_event_publication_requires_operator_and_never_accepts_future_or_invalid_feed(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-events",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        assert client.post("/api/v1/paper/events/feed", json={}).status_code == 401
        assert client.post("/api/v1/paper/events/feed", headers=HEADERS, json={}).status_code == 422
        assert client.get("/api/v1/events/feed").json()["status"] == "MISSING"


def test_committed_fill_and_pending_base_units_are_retained_without_changing_controls(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-events",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        quote = {
            "instrument_id": "EURUSD",
            "bid": "1.1",
            "ask": "1.1002",
            "observed_at": NOW.isoformat(),
            "world_state_version": "fixture",
        }
        assert client.post("/api/v1/paper/quotes", headers=HEADERS, json=quote).status_code == 200
        intent = {
            "intent_id": "position-1",
            "strategy_id": "demo",
            "strategy_version": "1",
            "instrument_id": "EURUSD",
            "side": "BUY",
            "desired_exposure": "1000",
            "risk_budget": "100",
            "max_spread": "0.0003",
            "max_slippage": "0.0002",
            "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
            "world_state_version": "fixture",
            "evidence_refs": ["fixture:1"],
            "created_at": NOW.isoformat(),
        }
        response = client.post(
            "/api/v1/paper/intents",
            headers=HEADERS | {"Idempotency-Key": "position-1"},
            json=intent,
        )
        assert response.status_code == 200 and response.json()["status"] == "ACCEPTED", (
            response.text
        )
        order_id = response.json()["order_id"]
        response = client.post(
            "/api/v1/paper/fills",
            headers=HEADERS,
            json={
                "fill_id": "fill-1",
                "order_id": order_id,
                "quantity": "250",
                "price": "1.1002",
                "fee": "0",
                "observed_at": NOW.isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        publish(client)
        safety = client.get("/api/v1/system/status").json()
        _, receipt = execute(client)
        row = next(
            row
            for row in json.loads(receipt["output_json"])["assessments"]
            if row["instrument_id"] == "EURUSD"
        )
        assert Decimal(row["filled_net_base_units"]) == 250
        assert Decimal(row["pending_buy_base_units"]) == 750
        assert row["has_local_unit_exposure"] is True and row["blackout_recommended"] is True
        assert client.get("/api/v1/system/status").json() == safety


@pytest.mark.parametrize(
    "age,ask,violation", [(6, "2", "STALE_MARKET"), (0, "2.002", "SPREAD_LIMIT")]
)
def test_actual_gateway_keeps_hard_quote_blocks_before_and_after_event_run(
    tmp_path, age, ask, violation
):
    with TestClient(
        create_app(
            operator_token="fixture-events",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        assert (
            client.post(
                "/api/v1/paper/quotes",
                headers=HEADERS,
                json={
                    "instrument_id": "EURUSD",
                    "bid": "2",
                    "ask": ask,
                    "observed_at": (NOW - timedelta(seconds=age)).isoformat(),
                    "world_state_version": "fixture",
                },
            ).status_code
            == 200
        )
        publish(client)
        for index in (0, 1):
            if index:
                _, receipt = execute(client)
                assert receipt["status"] == "COMPLETED"
            intent_id = f"blocked-{index}"
            intent = {
                "intent_id": intent_id,
                "strategy_id": "demo",
                "strategy_version": "1",
                "instrument_id": "EURUSD",
                "side": "BUY",
                "desired_exposure": "1",
                "risk_budget": "10",
                "max_spread": "0.0003",
                "max_slippage": "0.0002",
                "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
                "world_state_version": "fixture",
                "evidence_refs": ["fixture:1"],
                "created_at": NOW.isoformat(),
            }
            response = client.post(
                "/api/v1/paper/intents",
                headers=HEADERS | {"Idempotency-Key": intent_id},
                json=intent,
            )
            assert response.status_code == 200 and response.json()["status"] == "REJECT", (
                response.text
            )
            assert response.json()["order_id"] is None
            assert any(
                row["intent_id"] == intent_id and violation in row["reason"]
                for row in client.get("/api/v1/audit").json()["events"]
            )


def test_future_publication_does_not_replace_feed_and_clock_rollback_is_explicit(tmp_path):
    clock = [NOW]
    with TestClient(
        create_app(
            operator_token="fixture-events",
            clock=lambda: clock[0],
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        published = publish(client)
        future = client.get("/api/v1/events/feed").json()["feed"]
        future["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
        assert (
            client.post("/api/v1/paper/events/feed", headers=HEADERS, json=future).status_code
            == 409
        )
        assert client.get("/api/v1/events/feed").json()["feed_ref"] == published["feed_ref"]
        clock[0] = NOW - timedelta(seconds=1)
        assert client.get("/api/v1/events/feed").json()["status"] == "FUTURE"
        _, receipt = execute(client)
        output = json.loads(receipt["output_json"])
        assert output["feed_status"] == "FUTURE" and output["measurements"] == []
        assert all(row["blackout_recommended"] is None for row in output["assessments"])


def test_locked_event_archive_has_typed_unavailable_response_and_keeps_published_feed(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-events",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        published = publish(client)
        with sqlite3.connect(tmp_path / "paper.events.sqlite3") as blocker:
            blocker.execute("BEGIN EXCLUSIVE")
            assert client.get("/api/v1/events/feed").status_code == 503
            response = client.post(
                "/api/v1/paper/agents/context",
                headers=HEADERS,
                json={"agent_id": "A06", "agent_version": "0.2.0", "task_id": "blocked-source"},
            )
            assert response.status_code == 503
        assert client.get("/api/v1/events/feed").json()["feed_ref"] == published["feed_ref"]


def test_event_context_does_not_consume_unrelated_quote_arithmetic(tmp_path, monkeypatch):
    from ai_quant_os.world_state import WorldStateEngine

    def unavailable_world(*_args, **_kwargs):
        raise ValueError("Fixture unrelated world source unavailable")

    with TestClient(
        create_app(
            operator_token="fixture-events",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        publish(client)
        response = client.post(
            "/api/v1/paper/quotes",
            headers=HEADERS,
            json={
                "instrument_id": "EURUSD",
                "bid": "2",
                "ask": "2",
                "observed_at": NOW.isoformat(),
                "world_state_version": "unrelated",
            },
        )
        assert response.status_code == 200
        monkeypatch.setattr(WorldStateEngine, "snapshot", unavailable_world)
        _, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"

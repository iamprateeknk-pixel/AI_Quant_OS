from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
import pytest

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from ai_quant_os.api import create_app

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-liquidity"}


def publish(client, prices=("1", "2", "1", "2"), *, quote_age=0, ask=None):
    bars = [
        {
            "instrument_id": "EURUSD",
            "observed_at": (NOW - timedelta(seconds=len(prices) - index - 1)).isoformat(),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": "1",
        }
        for index, price in enumerate(prices)
    ]
    assert (
        client.post(
            "/api/v1/paper/world-state/bars", headers=HEADERS, json={"bars": bars}
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/paper/quotes",
        headers=HEADERS,
        json={
            "instrument_id": "EURUSD",
            "bid": prices[-1],
            "ask": ask or prices[-1],
            "observed_at": (NOW - timedelta(seconds=quote_age)).isoformat(),
            "world_state_version": "fixture",
        },
    )
    assert response.status_code == 200, response.text


def execute(client, run_id="liquidity-1"):
    response = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={"agent_id": "A05", "agent_version": "0.2.0", "task_id": "liquidity-task"},
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


def publish_depth(client, bid_size="300", ask_size="100", *, age=0, headers=HEADERS):
    return client.post(
        "/api/v1/paper/l1-quotes",
        headers=headers,
        json={
            "quote": {
                "instrument_id": "EURUSD",
                "bid": "2",
                "ask": "2.002",
                "observed_at": (NOW - timedelta(seconds=age)).isoformat(),
                "world_state_version": "fixture",
            },
            "bid_size": bid_size,
            "ask_size": ask_size,
        },
    )


def eur_assessment(receipt):
    return next(
        row
        for row in json.loads(receipt["output_json"])["assessments"]
        if row["instrument_id"] == "EURUSD"
    )


def test_supplied_synthetic_l1_sizes_are_measured_and_retained(tmp_path):
    args = dict(
        operator_token="fixture-liquidity",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        publish(client)
        response = publish_depth(client)
        assert response.status_code == 200, response.text
        _, receipt = execute(client)
        eur = eur_assessment(receipt)
        assert eur["liquidity_status"] == "SYNTHETIC_L1_OBSERVED"
        assert Decimal(eur["bid_displayed_base_units"]) == 300
        assert Decimal(eur["ask_displayed_base_units"]) == 100
        assert Decimal(eur["displayed_imbalance"]) == Decimal("0.5")
        assert eur["may_add_risk"] is False and eur["tradeability_status"] == "UNVERIFIED"
        from ai_quant_os.liquidity_agent import LiquidityState

        Draft202012Validator(LiquidityState.model_json_schema()).validate(
            json.loads(receipt["output_json"])
        )
    with TestClient(create_app(**args)) as client:
        assert (
            client.get("/api/v1/agents/graph-runs/liquidity-1", headers=HEADERS).json()["receipt"]
            == receipt
        )
        _, fresh = execute(client, "after-restart")
        assert eur_assessment(fresh)["liquidity_status"] == "UNAVAILABLE"


@pytest.mark.parametrize(
    "bid,ask,age,want_status,want_imbalance",
    [
        ("0", "0", 0, "SYNTHETIC_L1_OBSERVED", None),
        ("300", "100", 6, "UNAVAILABLE", None),
    ],
)
def test_zero_sizes_and_stale_quote_do_not_invent_usable_depth(
    tmp_path, bid, ask, age, want_status, want_imbalance
):
    with TestClient(
        create_app(
            operator_token="fixture-liquidity",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        assert publish_depth(client, bid, ask, age=age).status_code == 200
        _, receipt = execute(client)
        eur = eur_assessment(receipt)
        assert eur["liquidity_status"] == want_status
        assert eur["displayed_imbalance"] == want_imbalance


def test_quote_only_update_invalidates_depth_and_bad_ingress_does_not_publish(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-liquidity",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        assert publish_depth(client, headers={}).status_code == 401
        for bad in (
            "-1",
            "NaN",
            "Infinity",
            "1000000000001",
            "1e-1000000",
            "0e-1000000",
            "1." + "0" * 65,
        ):
            assert publish_depth(client, bad).status_code == 422
        _, empty = execute(client, "empty")
        assert eur_assessment(empty)["quote_status"] == "MISSING"
        assert publish_depth(client).status_code == 200
        publish(client, ask="2.004")
        _, receipt = execute(client)
        assert eur_assessment(receipt)["liquidity_status"] == "UNAVAILABLE"


@pytest.mark.parametrize(
    "quote_age,ask,violation", [(6, "2", "STALE_MARKET"), (0, "2.002", "SPREAD_LIMIT")]
)
def test_actual_order_gateway_keeps_hard_quote_blocks_before_and_after_agent(
    tmp_path, quote_age, ask, violation
):
    with TestClient(
        create_app(
            operator_token="fixture-liquidity",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        publish(client, quote_age=quote_age, ask=ask)
        for index in (0, 1):
            if index:
                _, receipt = execute(client)
                assert receipt["status"] == "COMPLETED"
            intent_id = f"blocked-{index}"
            response = client.post(
                "/api/v1/paper/intents",
                headers=HEADERS | {"Idempotency-Key": intent_id},
                json={
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
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "REJECT" and response.json()["order_id"] is None
            events = client.get("/api/v1/audit").json()["events"]
            assert any(
                row["intent_id"] == intent_id and violation in row["reason"] for row in events
            )


def test_source_spread_provenance_is_independent_of_callers_precision():
    from ai_quant_os.liquidity_sources import liquidity_sources
    from ai_quant_os.market import MarketSnapshot
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.world_state import WorldStateEngine

    quote = MarketSnapshot("EURUSD", Decimal("2"), Decimal("2.0000123456789"), NOW, "fixture", True)
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    world = engine.snapshot((quote,), now=NOW)
    with localcontext() as numeric:
        numeric.prec = 6
        assert set(liquidity_sources(world, {}, (quote,))) == {
            "quotes",
            "bars",
            "spreads",
            "session_state",
            "broker_telemetry",
        }


@pytest.mark.parametrize("bid", ["1e13", "1e-13", "2." + "0" * 65])
def test_l1_price_budget_is_rejected_before_publishing(tmp_path, bid):
    with TestClient(
        create_app(
            operator_token="fixture-liquidity",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        response = client.post(
            "/api/v1/paper/l1-quotes",
            headers=HEADERS,
            json={
                "quote": {
                    "instrument_id": "EURUSD",
                    "bid": bid,
                    "ask": bid,
                    "observed_at": NOW.isoformat(),
                    "world_state_version": "fixture",
                },
                "bid_size": "1",
                "ask_size": "1",
            },
        )
        assert response.status_code == 422
        _, receipt = execute(client)
        assert eur_assessment(receipt)["quote_status"] == "MISSING"


def test_real_graph_measures_prices_without_certifying_liquidity_and_retains_restart(tmp_path):
    args = dict(
        operator_token="fixture-liquidity",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        publish(client, ask="2.002")
        context, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"
        output = json.loads(receipt["output_json"])
        assert output["input_refs"] == output["evidence_refs"] == context["input_refs"]
        assert output["requires_escalation"] is True and Decimal(output["confidence"]) == 0
        eur = next(row for row in output["assessments"] if row["instrument_id"] == "EURUSD")
        assert eur["quote_usable"] is True and eur["history_usable"] is True
        assert abs(Decimal(eur["volatility"]["abnormality_score"]) - Decimal("0.5")) < Decimal(
            "1e-20"
        )
        assert Decimal(eur["spread_bps"]) > 0
        assert eur["liquidity_status"] == "UNAVAILABLE"
        assert eur["tradeability_status"] == "UNVERIFIED" and eur["may_add_risk"] is False
        assert output["hard_gate_authority"] == "RISK_KERNEL"
        from ai_quant_os.liquidity_agent import LiquidityState

        Draft202012Validator(LiquidityState.model_json_schema()).validate(output)
    with TestClient(create_app(**args)) as client:
        assert (
            client.get("/api/v1/agents/graph-runs/liquidity-1", headers=HEADERS).json()["receipt"]
            == receipt
        )


def test_missing_market_has_no_spread_or_volatility_and_never_claims_tradeability(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-liquidity",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        _, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"
        output = json.loads(receipt["output_json"])
        assert all(
            row["spread_bps"] is None
            and row["volatility"]["standard_deviation_bps"] is None
            and row["may_add_risk"] is False
            for row in output["assessments"]
        )


def test_stale_quote_is_not_usable_despite_fresh_bar_history(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-liquidity",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        publish(client, quote_age=6)
        _, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"
        eur = next(
            row
            for row in json.loads(receipt["output_json"])["assessments"]
            if row["instrument_id"] == "EURUSD"
        )
        assert eur["quote_status"] == "STALE" and eur["quote_usable"] is False
        assert eur["spread_bps"] is None and eur["history_usable"] is True

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, localcontext

import pytest
from hypothesis import HealthCheck, given, settings, strategies as hs

from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.portfolio import PaperFill

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow])
@given(
    hs.lists(
        hs.tuples(
            hs.integers(0, 3),
            hs.sampled_from(("BUY", "SELL")),
            hs.integers(1, 10),
            hs.integers(100, 120),
        ),
        min_size=1,
        max_size=20,
    )
)
def test_random_strategy_partitions_reconcile_without_netting_away_fees(terms):
    from ai_quant_os.paper_attribution import build_strategy_contributions

    owner = fixtures()[0][0]
    owners = tuple(
        replace(
            owner,
            order_id=str(i),
            intent_id=str(i),
            strategy_id=str(strategy),
            side=side,
            quantity=Decimal(quantity),
        )
        for i, (strategy, side, quantity, price) in enumerate(terms)
    )
    fills = tuple(
        PaperFill(
            str(i),
            str(i),
            "EURUSD",
            side,
            Decimal(quantity),
            Decimal(price) / 100,
            Decimal("0.01"),
            NOW,
        )
        for i, (strategy, side, quantity, price) in enumerate(terms)
    )
    result = build_strategy_contributions(
        owners, fills, (fixtures()[2],), PAPER_INSTRUMENTS, now=NOW
    )
    total = result.instruments[0]
    assert sum(row.marked_pnl_quote for row in result.strategies) == total.marked_pnl_quote
    assert total.fee_cash_flow_quote == -Decimal(len(fills)) / 100


def test_committed_paper_evidence_replays_from_its_source_snapshot():
    import json
    from pathlib import Path
    from pydantic import TypeAdapter
    from ai_quant_os.paper_attribution import (
        OrderAttribution,
        StrategyContributionReport,
        build_strategy_contributions,
    )
    from ai_quant_os.currency_portfolio import CurrencyInstrument

    artifact = json.loads(
        (Path(__file__).parents[1] / "docs/evaluations/paper-attribution-baseline.json").read_text()
    )
    owners = TypeAdapter(tuple[OrderAttribution, ...]).validate_python(artifact["owners"])
    fills = TypeAdapter(tuple[PaperFill, ...]).validate_python(artifact["fills"])
    quotes = TypeAdapter(tuple[MarketSnapshot, ...]).validate_python(artifact["quotes"])
    catalog = TypeAdapter(tuple[CurrencyInstrument, ...]).validate_python(artifact["catalog"])
    expected = TypeAdapter(StrategyContributionReport).validate_python(artifact["report"])
    assert (
        build_strategy_contributions(owners, fills, quotes, catalog, now=expected.as_of) == expected
    )
    assert expected.instruments[0].marked_pnl_quote == Decimal("1.3")


def fixtures():
    from ai_quant_os.paper_attribution import OrderAttribution

    owners = tuple(
        OrderAttribution(
            order_id=order,
            intent_id=order,
            strategy_id=strategy,
            strategy_version="1",
            instrument_id="EURUSD",
            side=side,
            quantity=Decimal(quantity),
            status="ACCEPTED",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
            intent_sha256="0" * 64,
            order_sha256="1" * 64,
        )
        for order, strategy, side, quantity in (
            ("buy", "alpha", "BUY", 10),
            ("sell", "beta", "SELL", 4),
        )
    )
    fills = (
        PaperFill("a", "buy", "EURUSD", "BUY", Decimal(10), Decimal("1.1"), Decimal("0.1"), NOW),
        PaperFill("b", "sell", "EURUSD", "SELL", Decimal(4), Decimal("1.2"), Decimal("0.2"), NOW),
    )
    quote = MarketSnapshot("EURUSD", Decimal("1.3"), Decimal("1.4"), NOW, "world", True)
    return owners, fills, quote


def report(owners=None, fills=None, quote=None, now=NOW):
    from ai_quant_os.paper_attribution import build_strategy_contributions

    original = fixtures()
    return build_strategy_contributions(
        owners or original[0],
        fills or original[1],
        (quote or original[2],),
        PAPER_INSTRUMENTS,
        now=now,
    )


def test_common_mark_reconciles_hedged_strategy_contributions_exactly():
    result = report()
    assert [row.strategy_id for row in result.strategies] == ["alpha", "beta"]
    assert [row.marked_pnl_quote for row in result.strategies] == [Decimal("1.9"), Decimal("-0.6")]
    total = result.instruments[0]
    assert total.net_quantity == 6
    assert total.mark_price == Decimal("1.3")
    assert total.fee_cash_flow_quote == Decimal("-0.3")
    assert total.marked_pnl_quote == Decimal("1.3")
    assert sum(row.marked_pnl_quote for row in result.strategies) == total.marked_pnl_quote
    assert result.reconciled is True and result.mode == "PAPER"
    assert "DECISION_PRICE_BENCHMARK_MISSING" in result.evidence_gaps


@pytest.mark.parametrize("change", ["stale", "future", "untrusted"])
def test_invalid_marks_withhold_pnl_without_losing_cash_accounting(change):
    owners, fills, quote = fixtures()
    if change == "stale":
        quote = replace(quote, observed_at=NOW - timedelta(seconds=6))
    elif change == "future":
        quote = replace(quote, observed_at=NOW + timedelta(seconds=1))
    else:
        quote = replace(quote, trusted=False)
    result = report(quote=quote)
    assert all(row.marked_pnl_quote is None for row in result.strategies)
    assert result.instruments[0].cash_flow_quote == Decimal("-6.5")
    assert result.instruments[0].marked_pnl_quote is None
    assert result.instruments[0].mark_state != "AVAILABLE"


@pytest.mark.parametrize("change", ["orphan", "duplicate", "overfill", "time", "side"])
def test_invalid_fill_lineage_cannot_be_attributed(change):
    owners, fills, quote = fixtures()
    if change == "orphan":
        fills = (replace(fills[0], order_id="unknown"), fills[1])
    elif change == "duplicate":
        fills = (*fills, fills[0])
    elif change == "overfill":
        fills = (replace(fills[0], quantity=Decimal(11)), fills[1])
    elif change == "time":
        fills = (replace(fills[0], observed_at=NOW - timedelta(seconds=1)), fills[1])
    else:
        fills = (replace(fills[0], side="SELL"), fills[1])
    with pytest.raises(ValueError):
        report(owners, fills, quote)


def test_strategy_versions_and_decimal_context_are_preserved():
    owners, fills, quote = fixtures()
    owners = (owners[0], replace(owners[1], strategy_id="alpha", strategy_version="2"))
    expected = report(owners, fills, quote)
    with localcontext() as context:
        context.prec = 4
        context.traps[Inexact] = True
        assert report(owners, fills, quote) == expected
    assert [(row.strategy_id, row.strategy_version) for row in expected.strategies] == [
        ("alpha", "1"),
        ("alpha", "2"),
    ]


def test_protected_api_reads_persisted_strategy_ownership_and_fill_snapshot(tmp_path):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app

    root = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=root)
    )
    headers = {"X-Operator-Token": "test-secret"}
    url = "/api/v1/paper/evidence/strategy-contributions"
    assert client.get(url).status_code == 401
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world",
    }
    assert client.post("/api/v1/paper/quotes", headers=headers, json=quote).status_code == 200
    intent = {
        "intent_id": "attribution",
        "strategy_id": "alpha",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "10",
        "risk_budget": "10",
        "max_spread": "0.001",
        "max_slippage": "0.001",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world",
        "evidence_refs": ["test:attribution"],
        "created_at": NOW.isoformat(),
    }
    response = client.post(
        "/api/v1/paper/intents", headers={**headers, "Idempotency-Key": "attribution"}, json=intent
    )
    assert response.status_code == 200, response.text
    fill = {
        "fill_id": "attribution-fill",
        "order_id": response.json()["order_id"],
        "quantity": "10",
        "price": "1.1",
        "fee": "0.1",
        "observed_at": NOW.isoformat(),
    }
    assert client.post("/api/v1/paper/fills", headers=headers, json=fill).status_code == 200
    evidence = client.get(url, headers=headers)
    assert evidence.status_code == 200, evidence.text
    record = evidence.json()
    assert record["strategies"][0]["strategy_id"] == "alpha"
    assert record["strategies"][0]["fee_cash_flow_quote"] == "-0.1"
    assert record["reconciled"] is True
    restarted = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=root)
    )
    later = restarted.get(url, headers=headers).json()
    assert later["ledger_sha256"] == record["ledger_sha256"]
    assert later["instruments"][0]["marked_pnl_quote"] is None


def test_partial_fills_and_short_jpy_marks_stay_in_their_quote_currency():
    from ai_quant_os.paper_attribution import build_strategy_contributions

    owners, fills, quote = fixtures()
    jpy_owner = replace(
        owners[1], order_id="jpy", intent_id="jpy", instrument_id="USDJPY", quantity=Decimal(2)
    )
    partial = (
        replace(fills[0], quantity=Decimal(4)),
        replace(fills[0], fill_id="partial", quantity=Decimal(6), fee=Decimal("0.2")),
    )
    jpy_fill = PaperFill(
        "jpy-fill", "jpy", "USDJPY", "SELL", Decimal(2), Decimal(152), Decimal(1), NOW
    )
    jpy_quote = MarketSnapshot("USDJPY", Decimal(150), Decimal(151), NOW, "world", True)
    result = build_strategy_contributions(
        (owners[0], jpy_owner), (*partial, jpy_fill), (quote, jpy_quote), PAPER_INSTRUMENTS, now=NOW
    )
    assert [(row.quote_currency, row.fill_count) for row in result.instruments] == [
        ("USD", 2),
        ("JPY", 1),
    ]
    assert result.instruments[1].mark_price == 151
    assert result.instruments[1].marked_pnl_quote == 1


def test_persisted_command_identity_cannot_change_attribution_ownership(tmp_path):
    import json
    import sqlite3
    from ai_quant_os.api import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(
            operator_token="test-secret",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    )
    headers = {"X-Operator-Token": "test-secret"}
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world",
    }
    client.post("/api/v1/paper/quotes", headers=headers, json=quote)
    intent = {
        "intent_id": "owner-tamper",
        "strategy_id": "alpha",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "10",
        "risk_budget": "10",
        "max_spread": "0.001",
        "max_slippage": "0.001",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world",
        "evidence_refs": ["test:attribution"],
        "created_at": NOW.isoformat(),
    }
    created = client.post(
        "/api/v1/paper/intents", headers={**headers, "Idempotency-Key": "owner-tamper"}, json=intent
    )
    assert created.status_code == 200
    with sqlite3.connect(tmp_path / "paper.sqlite3") as db:
        raw = json.loads(db.execute("SELECT order_json FROM paper_orders").fetchone()[0])
        raw["command"]["order_id"] = "changed"
        db.execute("UPDATE paper_orders SET order_json=?", (json.dumps(raw),))
    result = client.get("/api/v1/paper/evidence/strategy-contributions", headers=headers)
    assert result.status_code == 503

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, Inexact, localcontext
import json
import sqlite3

import pytest

from test_paper_attribution import NOW, fixtures


def benchmarks(owners):
    from ai_quant_os.execution_attribution import DecisionPriceBenchmark
    from ai_quant_os.market import MarketSnapshot

    quote = MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.1002"), NOW, "world", True)
    return tuple(
        DecisionPriceBenchmark(row.order_id, row.intent_id, "risk-" + row.order_id, NOW, quote)
        for row in owners
    )


def test_midpoint_spread_fill_difference_and_fees_reconcile():
    from ai_quant_os.execution_attribution import build_execution_contributions
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS

    owners, fills, _ = fixtures()
    fills = (replace(fills[0], price=Decimal("1.11")), replace(fills[1], price=Decimal("1.095")))
    result = build_execution_contributions(
        owners, fills, benchmarks(owners), PAPER_INSTRUMENTS, now=NOW
    )
    alpha, beta = result.strategies
    assert alpha.reference_mid_cash_flow_quote == Decimal("-11.001")
    assert alpha.spread_cash_adjustment_quote == Decimal("-0.001")
    assert alpha.decision_to_fill_cash_adjustment_quote == Decimal("-0.098")
    assert alpha.fee_cash_flow_quote == Decimal("-0.1")
    assert alpha.actual_cash_flow_quote == Decimal("-11.2")
    assert beta.decision_to_fill_cash_adjustment_quote == Decimal("-0.02")
    assert result.instruments[0].actual_cash_flow_quote == Decimal("-7.02")
    assert result.coverage == "COMPLETE" and result.reconciled is True
    with localcontext() as context:
        context.prec = 4
        context.traps[Inexact] = True
        assert (
            build_execution_contributions(
                owners, fills, benchmarks(owners), PAPER_INSTRUMENTS, now=NOW
            )
            == result
        )


def test_legacy_missing_benchmark_withholds_decomposition_not_actual_cash():
    from ai_quant_os.execution_attribution import build_execution_contributions
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS

    owners, fills, _ = fixtures()
    result = build_execution_contributions(
        owners, fills, benchmarks(owners)[:1], PAPER_INSTRUMENTS, now=NOW
    )
    assert result.coverage == "PARTIAL"
    assert result.strategies[1].reference_mid_cash_flow_quote is None
    assert result.instruments[0].decision_to_fill_cash_adjustment_quote is None
    assert result.instruments[0].actual_cash_flow_quote == Decimal("-6.5")
    assert result.missing_benchmark_order_ids == ("sell",)


def test_cash_attribution_refuses_silent_decimal_rounding():
    from ai_quant_os.execution_attribution import build_execution_contributions
    from ai_quant_os.paper_attribution import build_strategy_contributions
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS

    owners, fills, quote = fixtures()
    fills = (replace(fills[0], price=Decimal("1.100000000000000000000000000001")), fills[1])
    with pytest.raises(ArithmeticError):
        build_strategy_contributions(owners, fills, (quote,), PAPER_INSTRUMENTS, now=NOW)
    with pytest.raises(ArithmeticError):
        build_execution_contributions(owners, fills, benchmarks(owners), PAPER_INSTRUMENTS, now=NOW)


def test_committed_execution_snapshot_replays_retained_initial_prices():
    from pathlib import Path
    from pydantic import TypeAdapter
    from ai_quant_os.execution_attribution import (
        DecisionPriceBenchmark,
        ExecutionContributionReport,
        build_execution_contributions,
    )
    from ai_quant_os.paper_attribution import OrderAttribution
    from ai_quant_os.portfolio import PaperFill
    from ai_quant_os.currency_portfolio import CurrencyInstrument

    artifact = json.loads(
        (
            Path(__file__).parents[1] / "docs/evaluations/execution-attribution-baseline.json"
        ).read_text()
    )
    owners = TypeAdapter(tuple[OrderAttribution, ...]).validate_python(artifact["owners"])
    fills = TypeAdapter(tuple[PaperFill, ...]).validate_python(artifact["fills"])
    references = TypeAdapter(tuple[DecisionPriceBenchmark, ...]).validate_python(
        artifact["benchmarks"]
    )
    catalog = TypeAdapter(tuple[CurrencyInstrument, ...]).validate_python(artifact["catalog"])
    expected = TypeAdapter(ExecutionContributionReport).validate_python(artifact["report"])
    assert (
        build_execution_contributions(owners, fills, references, catalog, now=expected.as_of)
        == expected
    )
    assert expected.instruments[0].actual_cash_flow_quote == Decimal("-7.02")


@pytest.mark.parametrize("kind", ["future", "instrument", "intent", "duplicate"])
def test_misbound_or_lookahead_benchmarks_are_rejected(kind):
    from ai_quant_os.execution_attribution import build_execution_contributions
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS

    owners, fills, _ = fixtures()
    rows = benchmarks(owners)
    if kind == "future":
        rows = (replace(rows[0], decision_at=NOW + timedelta(seconds=1)), rows[1])
    elif kind == "instrument":
        rows = (replace(rows[0], market=replace(rows[0].market, instrument_id="GBPUSD")), rows[1])
    elif kind == "intent":
        rows = (replace(rows[0], intent_id="wrong"), rows[1])
    else:
        rows = (*rows, rows[0])
    with pytest.raises(ValueError):
        build_execution_contributions(owners, fills, rows, PAPER_INSTRUMENTS, now=NOW)


def paper_gateway(tmp_path, broker=None):
    from ai_quant_os.broker import BrokerRouter
    from ai_quant_os.execution import PaperExecutionGateway
    from ai_quant_os.risk import RiskKernel, RiskLimits

    risk = RiskKernel(RiskLimits(Decimal(1000), Decimal(1500), 5))
    gateway = PaperExecutionGateway(
        risk,
        store_path=tmp_path / "paper.sqlite3",
        router=BrokerRouter([broker]) if broker else None,
    )
    return risk, gateway


def intent_and_quote():
    from ai_quant_os.contracts import TradeIntent
    from ai_quant_os.market import MarketSnapshot

    intent = TradeIntent(
        "initial-price",
        "alpha",
        "1",
        "EURUSD",
        "BUY",
        Decimal(10),
        Decimal(10),
        Decimal("0.001"),
        Decimal("0.001"),
        NOW + timedelta(minutes=1),
        "world",
        (),
        NOW,
    )
    return intent, MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.1002"), NOW, "world", True)


def test_initial_benchmark_is_durable_before_broker_dispatch(tmp_path):
    from ai_quant_os.broker import SimulatedBroker

    class InspectingBroker(SimulatedBroker):
        def place(self, command):
            with sqlite3.connect(tmp_path / "paper.sqlite3") as db:
                row = db.execute(
                    "SELECT benchmark_json FROM paper_price_benchmarks WHERE order_id=?",
                    (command.order_id,),
                ).fetchone()
                assert row is not None
                assert json.loads(row[0])["market"]["ask"] == "1.1002"
            return super().place(command)

    risk, gateway = paper_gateway(
        tmp_path, InspectingBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    )
    intent, quote = intent_and_quote()
    decision = risk.evaluate(intent, quote, now=NOW)
    order = gateway.submit(intent, decision, idempotency_key="initial", now=NOW)
    assert order.status == "ACCEPTED"
    owners, fills, rows = gateway.execution_evidence_ledger
    assert len(rows) == 1 and rows[0].risk_id == decision.risk_id
    assert rows[0].market == quote
    assert gateway.submit(intent, decision, idempotency_key="initial", now=NOW) == order
    restarted = paper_gateway(tmp_path)[1]
    assert restarted.execution_evidence_ledger[2] == rows


def test_benchmark_failure_rolls_back_order_without_dispatch(tmp_path, monkeypatch):
    risk, gateway = paper_gateway(tmp_path)
    intent, quote = intent_and_quote()
    decision = risk.evaluate(intent, quote, now=NOW)

    def unavailable(*args):
        raise sqlite3.OperationalError("Benchmark unavailable")

    monkeypatch.setattr(gateway._store, "_record_benchmark_unlocked", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        gateway.submit(intent, decision, idempotency_key="initial", now=NOW)
    assert gateway.orders == ()


def test_kernel_benchmark_rejects_forged_and_consumed_decisions(tmp_path):
    risk, gateway = paper_gateway(tmp_path)
    intent, quote = intent_and_quote()
    decision = risk.evaluate(intent, quote, now=NOW)
    assert risk.decision_market(decision) == quote
    with pytest.raises(ValueError):
        risk.decision_market(replace(decision, approved_exposure=Decimal(11)))
    gateway.submit(intent, decision, idempotency_key="initial", now=NOW)
    with pytest.raises(ValueError):
        risk.decision_market(decision)


def api_account(tmp_path):
    from ai_quant_os.api import create_app
    from fastapi.testclient import TestClient
    from pydantic import TypeAdapter
    from ai_quant_os.contracts import TradeIntent

    intent, quote = intent_and_quote()
    client = TestClient(
        create_app(
            operator_token="test-secret",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    )
    headers = {"X-Operator-Token": "test-secret"}
    payload = {
        "instrument_id": quote.instrument_id,
        "bid": str(quote.bid),
        "ask": str(quote.ask),
        "observed_at": NOW.isoformat(),
        "world_state_version": "world",
    }
    assert client.post("/api/v1/paper/quotes", headers=headers, json=payload).status_code == 200
    created = client.post(
        "/api/v1/paper/intents",
        headers={**headers, "Idempotency-Key": "benchmark-api"},
        json=json.loads(TypeAdapter(TradeIntent).dump_json(intent)),
    )
    assert created.status_code == 200, created.text
    fill = {
        "fill_id": "benchmark-fill",
        "order_id": created.json()["order_id"],
        "quantity": "10",
        "price": "1.11",
        "fee": "0.1",
        "observed_at": NOW.isoformat(),
    }
    assert client.post("/api/v1/paper/fills", headers=headers, json=fill).status_code == 200
    return client, headers


def test_protected_execution_evidence_does_not_need_current_quotes_after_restart(tmp_path):
    from ai_quant_os.api import create_app
    from fastapi.testclient import TestClient

    client, headers = api_account(tmp_path)
    url = "/api/v1/paper/evidence/execution-contributions"
    assert client.get(url).status_code == 401
    initial = client.get(url, headers=headers)
    assert initial.status_code == 200, initial.text
    record = initial.json()
    assert record["coverage"] == "COMPLETE"
    assert Decimal(record["strategies"][0]["decision_to_fill_cash_adjustment_quote"]) == Decimal(
        "-0.098"
    )
    restarted = TestClient(
        create_app(
            operator_token="test-secret",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    )
    assert restarted.get(url, headers=headers).json() == record


@pytest.mark.parametrize("kind", ["deleted", "changed", "legacy", "flag"])
def test_benchmark_corruption_is_not_silently_treated_as_legacy(tmp_path, kind):
    client, headers = api_account(tmp_path)
    with sqlite3.connect(tmp_path / "paper.sqlite3") as db:
        if kind == "changed":
            db.execute("UPDATE paper_price_benchmarks SET benchmark_json='{}'")
        else:
            db.execute("DELETE FROM paper_price_benchmarks")
            if kind == "legacy":
                # Simulate the additive migration's existing-order default, not a fresh quote.
                db.execute("UPDATE paper_orders SET benchmark_required=0")
            elif kind == "flag":
                db.execute("UPDATE paper_orders SET benchmark_required=2")
    response = client.get("/api/v1/paper/evidence/execution-contributions", headers=headers)
    if kind == "legacy":
        assert response.status_code == 200
        assert response.json()["coverage"] == "NONE"
        assert response.json()["instruments"][0]["reference_mid_cash_flow_quote"] is None
    else:
        assert response.status_code == 503

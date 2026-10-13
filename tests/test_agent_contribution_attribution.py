from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ai_quant_os.agent_runs import AgentRun
from ai_quant_os.decision_gateway import DecisionGateway
from ai_quant_os.deep_review import DeepReviewDispatcher
from ai_quant_os.paper_attribution import OrderAttribution
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.portfolio import PaperFill
from ai_quant_os.world_state import WorldStateEngine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def archived_run():
    import asyncio

    async def create():
        snapshot = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
        decision = await DecisionGateway(clock=lambda: NOW).classify(
            snapshot, instrument_id="EURUSD", task_id="agent-attribution"
        )
        review = await DeepReviewDispatcher(clock=lambda: NOW).dispatch(snapshot, decision)
        return AgentRun(
            schema_version="1.0",
            run_id=decision.trace_id,
            snapshot_ref=snapshot.world_state_version,
            decision=decision,
            deep_review=review,
            recorded_at=NOW,
        )

    return asyncio.run(create())


def test_associates_only_verified_run_refs_and_reconciles_selected_fills():
    from ai_quant_os.agent_attribution import build_agent_contributions

    run = archived_run()
    owner = OrderAttribution(
        order_id="order-1",
        intent_id="intent-1",
        strategy_id="alpha",
        strategy_version="1.0.0",
        instrument_id="EURUSD",
        side="BUY",
        quantity=Decimal("2"),
        status="ACCEPTED",
        created_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=4),
        intent_sha256="0" * 64,
        order_sha256="1" * 64,
    )
    fill = PaperFill(
        "fill-1",
        "order-1",
        "EURUSD",
        "BUY",
        Decimal("2"),
        Decimal("1.2"),
        Decimal("0.1"),
        NOW + timedelta(seconds=2),
    )

    report = build_agent_contributions(
        (owner,),
        (fill,),
        {"order-1": (run.run_id,)},
        {run.run_id: run},
        PAPER_INSTRUMENTS,
    )

    assert report.coverage == "COMPLETE"
    assert report.associations[0].run_id == run.run_id
    assert report.associations[0].fill_count == 1
    assert report.associations[0].fee_cash_flow_quote == Decimal("-0.1")
    assert report.reconciled is True
    assert report.causal_attribution is False


def test_marked_agent_contributions_reconcile_using_common_fresh_liquidation_mark():
    from ai_quant_os.agent_attribution import build_marked_agent_contributions

    run = archived_run()
    owner = OrderAttribution(
        order_id="order-1",
        intent_id="intent-1",
        strategy_id="alpha",
        strategy_version="1.0.0",
        instrument_id="EURUSD",
        side="BUY",
        quantity=Decimal("2"),
        status="ACCEPTED",
        created_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=4),
        intent_sha256="0" * 64,
        order_sha256="1" * 64,
    )
    fill = PaperFill(
        "fill-1",
        "order-1",
        "EURUSD",
        "BUY",
        Decimal("2"),
        Decimal("1.2"),
        Decimal("0.1"),
        NOW + timedelta(seconds=2),
    )
    quote = MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.1002"), NOW, "world", True)

    report = build_marked_agent_contributions(
        (owner,),
        (fill,),
        {"order-1": (run.run_id,)},
        {run.run_id: run},
        PAPER_INSTRUMENTS,
        (quote,),
        now=NOW,
    )

    assert report.schema_version == "2.0"
    assert report.associations[0].net_quantity == Decimal("2")
    assert report.associations[0].cash_flow_quote == Decimal("-2.5")
    assert report.associations[0].mark_price == Decimal("1.1")
    assert report.associations[0].mark_observed_at == NOW
    assert report.associations[0].mark_world_state_version == "world"
    assert report.associations[0].marked_inventory_quote == Decimal("2.2")
    assert report.associations[0].marked_pnl_quote == Decimal("-0.3")
    assert report.instruments[0].marked_pnl_quote == report.associations[0].marked_pnl_quote
    assert report.reconciled is True
    assert report.causal_attribution is False


def test_marked_agent_contributions_withhold_stale_marks_but_keep_cash_reconciled():
    from ai_quant_os.agent_attribution import build_marked_agent_contributions

    run = archived_run()
    owner = OrderAttribution(
        order_id="order-1",
        intent_id="intent-1",
        strategy_id="alpha",
        strategy_version="1.0.0",
        instrument_id="EURUSD",
        side="BUY",
        quantity=Decimal("2"),
        status="ACCEPTED",
        created_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=4),
        intent_sha256="0" * 64,
        order_sha256="1" * 64,
    )
    fill = PaperFill(
        "fill-1",
        "order-1",
        "EURUSD",
        "BUY",
        Decimal("2"),
        Decimal("1.2"),
        Decimal("0.1"),
        NOW + timedelta(seconds=2),
    )
    quote = MarketSnapshot(
        "EURUSD", Decimal("1.1"), Decimal("1.1002"), NOW - timedelta(seconds=6), "world", True
    )

    report = build_marked_agent_contributions(
        (owner,),
        (fill,),
        {"order-1": (run.run_id,)},
        {run.run_id: run},
        PAPER_INSTRUMENTS,
        (quote,),
        now=NOW,
    )

    assert report.associations[0].mark_state == "STALE"
    assert report.associations[0].mark_price is None
    assert report.associations[0].marked_inventory_quote is None
    assert report.associations[0].marked_pnl_quote is None
    assert report.associations[0].cash_flow_quote == Decimal("-2.5")
    assert "CURRENT_MARK_EVIDENCE_MISSING" in report.evidence_gaps


def test_marked_agent_report_excludes_owner_with_rejected_run_timing():
    from ai_quant_os.agent_attribution import build_marked_agent_contributions

    run = archived_run()

    def make_owner(order_id, created_at, quantity):
        return OrderAttribution(
            order_id=order_id,
            intent_id=f"intent-{order_id}",
            strategy_id="alpha",
            strategy_version="1.0.0",
            instrument_id="EURUSD",
            side="BUY",
            quantity=Decimal(quantity),
            status="ACCEPTED",
            created_at=created_at,
            expires_at=NOW + timedelta(seconds=8),
            intent_sha256="0" * 64,
            order_sha256="1" * 64,
        )

    valid = make_owner("valid", NOW + timedelta(seconds=1), "1")
    early = make_owner("early", NOW - timedelta(seconds=1), "3")
    fills = (
        PaperFill(
            "valid-fill",
            "valid",
            "EURUSD",
            "BUY",
            Decimal("1"),
            Decimal("1.2"),
            Decimal("0"),
            NOW + timedelta(seconds=2),
        ),
        PaperFill(
            "early-fill", "early", "EURUSD", "BUY", Decimal("3"), Decimal("1.2"), Decimal("0"), NOW
        ),
    )

    report = build_marked_agent_contributions(
        (valid, early),
        fills,
        {"valid": (run.run_id,), "early": (run.run_id,)},
        {run.run_id: run},
        PAPER_INSTRUMENTS,
        (),
        now=NOW + timedelta(seconds=3),
    )

    assert report.associations[0].fill_count == 1
    assert report.associations[0].net_quantity == Decimal("1")
    assert report.unattributed_fill_ids == ("early-fill",)


def test_marked_agent_rows_share_portfolio_liquidation_side_and_sum_exactly():
    from ai_quant_os.agent_attribution import build_marked_agent_contributions

    buy_run, sell_run = archived_run(), archived_run()
    owners = (
        OrderAttribution(
            "buy",
            "buy-intent",
            "a",
            "1",
            "EURUSD",
            "BUY",
            Decimal("2"),
            "ACCEPTED",
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=8),
            "0" * 64,
            "1" * 64,
        ),
        OrderAttribution(
            "sell",
            "sell-intent",
            "b",
            "1",
            "EURUSD",
            "SELL",
            Decimal("3"),
            "ACCEPTED",
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=8),
            "2" * 64,
            "3" * 64,
        ),
    )
    fills = (
        PaperFill(
            "buy-fill",
            "buy",
            "EURUSD",
            "BUY",
            Decimal("2"),
            Decimal("1.2"),
            Decimal("0.1"),
            NOW + timedelta(seconds=2),
        ),
        PaperFill(
            "sell-fill",
            "sell",
            "EURUSD",
            "SELL",
            Decimal("3"),
            Decimal("1.3"),
            Decimal("0.15"),
            NOW + timedelta(seconds=2),
        ),
    )
    refs = {"buy": (buy_run.run_id,), "sell": (sell_run.run_id,)}
    quote = MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.1002"), NOW, "world", True)

    report = build_marked_agent_contributions(
        owners,
        fills,
        refs,
        {buy_run.run_id: buy_run, sell_run.run_id: sell_run},
        PAPER_INSTRUMENTS,
        (quote,),
        now=NOW,
    )

    assert len(report.associations) == 2
    assert {row.mark_price for row in report.associations} == {Decimal("1.1002")}
    assert report.instruments[0].net_quantity == Decimal("-1")
    assert report.instruments[0].cash_flow_quote == Decimal("1.25")
    assert report.instruments[0].marked_inventory_quote == Decimal("-1.1002")
    assert report.instruments[0].marked_pnl_quote == Decimal("0.1498")
    assert sum((row.marked_pnl_quote for row in report.associations), Decimal(0)) == Decimal(
        "0.1498"
    )


def test_unlinked_and_ambiguous_orders_are_withheld_from_agent_totals():
    from ai_quant_os.agent_attribution import build_agent_contributions

    run = archived_run()
    owner = OrderAttribution(
        order_id="order-1",
        intent_id="intent-1",
        strategy_id="alpha",
        strategy_version="1.0.0",
        instrument_id="EURUSD",
        side="BUY",
        quantity=Decimal("2"),
        status="ACCEPTED",
        created_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=4),
        intent_sha256="0" * 64,
        order_sha256="1" * 64,
    )
    fill = PaperFill(
        "fill-1",
        "order-1",
        "EURUSD",
        "BUY",
        Decimal("2"),
        Decimal("1.2"),
        Decimal("0.1"),
        NOW + timedelta(seconds=2),
    )

    report = build_agent_contributions(
        (owner,), (fill,), {"order-1": ()}, {run.run_id: run}, PAPER_INSTRUMENTS
    )

    assert report.coverage == "NONE"
    assert report.associations == ()
    assert report.unlinked_order_ids == ("order-1",)
    assert report.unattributed_fill_ids == ("fill-1",)

    ambiguous = build_agent_contributions(
        (owner,),
        (fill,),
        {"order-1": (run.run_id, "missing-run")},
        {run.run_id: run},
        PAPER_INSTRUMENTS,
    )
    assert ambiguous.coverage == "NONE"
    assert ambiguous.ambiguous_order_ids == ("order-1",)
    assert ambiguous.missing_run_ids == ("missing-run",)
    assert ambiguous.unattributed_fill_ids == ("fill-1",)


def test_protected_api_reports_archived_agent_run_to_paper_fill_association():
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app

    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    headers = {"X-Operator-Token": "fixture-token"}
    classified = client.post(
        "/api/v1/paper/decisions/classify",
        headers=headers,
        json={"instrument_id": "EURUSD", "task_id": "agent-attribution"},
    )
    assert classified.status_code == 200
    decision = classified.json()
    client.post(
        "/api/v1/paper/quotes",
        headers=headers,
        json={
            "instrument_id": "EURUSD",
            "bid": "1.1000",
            "ask": "1.1002",
            "observed_at": NOW.isoformat(),
            "world_state_version": decision["world_state_version"],
        },
    )
    intent = client.post(
        "/api/v1/paper/intents",
        headers={**headers, "Idempotency-Key": "agent-linked-order"},
        json={
            "intent_id": "agent-linked-intent",
            "strategy_id": "alpha",
            "strategy_version": "1.0.0",
            "instrument_id": "EURUSD",
            "side": "BUY",
            "desired_exposure": "2",
            "risk_budget": "10",
            "max_spread": "0.001",
            "max_slippage": "0.001",
            "valid_until": (NOW + timedelta(seconds=4)).isoformat(),
            "world_state_version": decision["world_state_version"],
            "evidence_refs": [f"agent-run:{decision['trace_id']}"],
            "created_at": NOW.isoformat(),
        },
    )
    assert intent.status_code == 200
    client.post(
        "/api/v1/paper/fills",
        headers=headers,
        json={
            "fill_id": "agent-linked-fill",
            "order_id": intent.json()["order_id"],
            "quantity": "2",
            "price": "1.1001",
            "fee": "0.1",
            "observed_at": NOW.isoformat(),
        },
    )

    report = client.get("/api/v1/paper/evidence/agent-contributions", headers=headers)

    assert report.status_code == 200
    body = report.json()
    assert body["coverage"] == "COMPLETE"
    assert body["associations"][0]["run_id"] == decision["trace_id"]
    assert body["associations"][0]["actual_cash_flow_quote"] == "-2.3002"
    assert body["causal_attribution"] is False
    assert body["promotion_allowed"] is False
    assert "no-store" in report.headers["cache-control"]

    marked = client.get("/api/v1/paper/evidence/agent-contributions/marked", headers=headers)
    assert marked.status_code == 200
    marked_body = marked.json()
    assert marked_body["schema_version"] == "2.0"
    assert marked_body["associations"][0]["mark_state"] == "AVAILABLE"
    assert marked_body["associations"][0]["net_quantity"] == "2"
    assert marked_body["associations"][0]["marked_inventory_quote"] == "2.2000"
    assert marked_body["associations"][0]["marked_pnl_quote"] == "-0.1002"
    assert marked_body["reconciled"] is True
    assert marked_body["causal_attribution"] is False
    assert "no-store" in marked.headers["cache-control"]


def test_agent_contribution_api_requires_operator_session():
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app

    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))

    response = client.get("/api/v1/paper/evidence/agent-contributions")
    marked_response = client.get("/api/v1/paper/evidence/agent-contributions/marked")

    assert response.status_code == 401
    assert marked_response.status_code == 401


def test_agent_contribution_schema_matches_runtime_report():
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator
    from pydantic import TypeAdapter

    from ai_quant_os.agent_attribution import AgentContributionReport

    path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "schemas"
        / "agent-contribution-report-v1.schema.json"
    )
    actual = json.loads(path.read_text(encoding="utf-8"))
    expected = TypeAdapter(AgentContributionReport).json_schema(mode="validation")
    expected.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:ai-quant-os:report:agent-contribution:1.0",
        }
    )
    assert actual == expected
    Draft202012Validator.check_schema(actual)

    marked_path = path.with_name("agent-marked-contribution-report-v2.schema.json")
    marked_actual = json.loads(marked_path.read_text(encoding="utf-8"))
    from ai_quant_os.agent_attribution import AgentMarkedContributionReport

    marked_expected = TypeAdapter(AgentMarkedContributionReport).json_schema(mode="validation")
    marked_expected.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:ai-quant-os:report:agent-contribution:2.0",
        }
    )
    assert marked_actual == marked_expected
    Draft202012Validator.check_schema(marked_actual)

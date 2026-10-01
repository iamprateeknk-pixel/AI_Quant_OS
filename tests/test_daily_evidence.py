from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest


DAY = date(2026, 1, 2)
START = datetime(2026, 1, 2, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def checkpoints():
    from ai_quant_os.daily_evidence import EvidenceCheckpoint
    from ai_quant_os.paper_attribution import OrderAttribution
    from ai_quant_os.portfolio import PaperFill
    from ai_quant_os.market import MarketSnapshot
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS

    buy = OrderAttribution(
        "buy",
        "buy-intent",
        "alpha",
        "1",
        "EURUSD",
        "BUY",
        Decimal(10),
        "ACCEPTED",
        START - timedelta(hours=2),
        END,
        "a" * 64,
        "b" * 64,
    )
    sell = OrderAttribution(
        "sell",
        "sell-intent",
        "beta",
        "1",
        "EURUSD",
        "SELL",
        Decimal(4),
        "ACCEPTED",
        START + timedelta(hours=1),
        END + timedelta(hours=1),
        "c" * 64,
        "d" * 64,
    )
    opening_fill = PaperFill(
        "before",
        "buy",
        "EURUSD",
        "BUY",
        Decimal(10),
        Decimal(1),
        Decimal("0.1"),
        START - timedelta(hours=1),
    )
    day_fill = PaperFill(
        "during",
        "sell",
        "EURUSD",
        "SELL",
        Decimal(4),
        Decimal("1.2"),
        Decimal("0.2"),
        START + timedelta(hours=2),
    )
    opening = EvidenceCheckpoint(
        "open",
        START,
        (buy,),
        (opening_fill,),
        (MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.11"), START, "open", True),),
        PAPER_INSTRUMENTS,
    )
    closing = EvidenceCheckpoint(
        "close",
        END,
        (buy, sell),
        (opening_fill, day_fill),
        (MarketSnapshot("EURUSD", Decimal("1.3"), Decimal("1.31"), END, "close", True),),
        PAPER_INSTRUMENTS,
    )
    return opening, closing


def test_daily_reconciles_opening_cash_closing_and_strategy_contributions():
    from ai_quant_os.daily_evidence import build_daily_report

    opening, closing = checkpoints()
    report = build_daily_report(DAY, opening, closing)
    assert report.state == "COMPLETE_ACCOUNTING"
    assert report.promotion_allowed is False
    total = report.instruments[0]
    assert total.opening_quantity == 10
    assert total.closing_quantity == 6
    assert total.fill_count == 1
    assert total.cash_flow_quote == Decimal("4.6")
    assert total.fee_cash_flow_quote == Decimal("-0.2")
    assert total.opening_inventory_quote == Decimal(11)
    assert total.closing_inventory_quote == Decimal("7.8")
    assert total.daily_pnl_quote == Decimal("1.4")
    assert sum(row.daily_pnl_quote for row in report.strategies) == total.daily_pnl_quote
    assert [row.daily_pnl_quote for row in report.strategies] == [Decimal(2), Decimal("-0.6")]


@pytest.mark.parametrize(
    "change,state",
    [
        ("missing", "MISSING"),
        ("stale", "STALE"),
        ("future", "FUTURE"),
        ("untrusted", "UNTRUSTED"),
        ("late", "CAPTURE_LATE"),
    ],
)
def test_boundary_marks_are_withheld_without_inventing_prices(change, state):
    from ai_quant_os.daily_evidence import build_daily_report

    opening, closing = checkpoints()
    quote = opening.quotes[0]
    if change == "missing":
        opening = replace(opening, quotes=())
    elif change == "late":
        opening = replace(opening, captured_at=START + timedelta(seconds=10))
    else:
        quote = replace(
            quote,
            observed_at=START - timedelta(seconds=6)
            if change == "stale"
            else START + timedelta(seconds=1)
            if change == "future"
            else START,
            trusted=change != "untrusted",
        )
        opening = replace(opening, captured_at=START + timedelta(seconds=2), quotes=(quote,))
    report = build_daily_report(DAY, opening, closing)
    assert report.state == "INCOMPLETE_MARKS"
    assert report.instruments[0].opening_mark_state == state
    assert report.instruments[0].daily_pnl_quote is None
    assert report.instruments[0].cash_flow_quote == Decimal("4.6")


def test_utc_half_open_day_and_late_opening_ledger_changes():
    from ai_quant_os.daily_evidence import build_daily_report

    opening, closing = checkpoints()
    at_start = replace(closing.fills[1], observed_at=START)
    sell = replace(closing.owners[1], created_at=START - timedelta(seconds=1))
    closing = replace(closing, owners=(closing.owners[0], sell), fills=(closing.fills[0], at_start))
    assert build_daily_report(DAY, opening, closing).instruments[0].fill_count == 1
    at_end = replace(at_start, observed_at=END)
    closing = replace(closing, fills=(closing.fills[0], at_end))
    assert build_daily_report(DAY, opening, closing).instruments[0].fill_count == 0
    changed = replace(closing.fills[0], price=Decimal("1.01"))
    with pytest.raises(ValueError, match="opening ledger"):
        build_daily_report(DAY, opening, replace(closing, fills=(changed, at_end)))


def test_daily_archive_is_immutable_restarts_and_detects_corruption(tmp_path):
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.evidence_store import EvidenceStore

    opening, closing = checkpoints()
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    assert store.checkpoint(opening) == opening
    store.checkpoint(closing)
    report = build_daily_report(DAY, opening, closing)
    evidence = DailyEvidence(opening, closing, report, END)
    assert store.publish(evidence) == evidence
    assert store.publish(evidence) == evidence
    assert EvidenceStore(store.path).daily(DAY) == evidence
    with pytest.raises(ValueError):
        replace(evidence, report=replace(report, state="INCOMPLETE_MARKS"))
    with pytest.raises(ValueError, match="Immutable"):
        store.checkpoint(replace(opening, quotes=()))
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE daily_evidence SET blob=?", (b"{}",))
    with pytest.raises(ValueError, match="fingerprint"):
        store.daily(DAY)


def test_daily_rejects_open_window_unknown_currency_and_inexact_arithmetic():
    from decimal import localcontext
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report

    opening, closing = checkpoints()
    with pytest.raises(ValueError):
        build_daily_report(
            DAY, opening, replace(closing, captured_at=END - timedelta(seconds=1), quotes=())
        )
    with pytest.raises(ValueError):
        DailyEvidence(opening, closing, build_daily_report(DAY, opening, closing), START)
    with localcontext() as context:
        context.prec = 3
        assert build_daily_report(DAY, opening, closing).instruments[0].daily_pnl_quote == Decimal(
            "1.4"
        )
    with pytest.raises(ValueError):
        replace(closing, catalog=())
    with pytest.raises(ArithmeticError):
        replace(
            closing,
            fills=(
                closing.fills[0],
                replace(closing.fills[1], price=Decimal("1.234567891234567891234567891234")),
            ),
        )


def test_daily_keeps_quote_currencies_separate_and_flat_days_need_no_mark():
    from ai_quant_os.daily_evidence import build_daily_report

    opening, closing = checkpoints()
    jpy_owners = tuple(
        replace(
            owner,
            order_id="jpy-" + owner.order_id,
            intent_id="jpy-" + owner.intent_id,
            instrument_id="EURJPY",
        )
        for owner in closing.owners
    )
    jpy_fills = tuple(
        replace(
            fill,
            fill_id="jpy-" + fill.fill_id,
            order_id="jpy-" + fill.order_id,
            instrument_id="EURJPY",
            price=Decimal(158 if i == 0 else 160),
        )
        for i, fill in enumerate(closing.fills)
    )
    oq = replace(opening.quotes[0], instrument_id="EURJPY", bid=Decimal(159), ask=Decimal(160))
    cq = replace(closing.quotes[0], instrument_id="EURJPY", bid=Decimal(161), ask=Decimal(162))
    opening = replace(
        opening,
        owners=opening.owners + (jpy_owners[0],),
        fills=opening.fills + (jpy_fills[0],),
        quotes=opening.quotes + (oq,),
    )
    closing = replace(
        closing,
        owners=closing.owners + jpy_owners,
        fills=closing.fills + jpy_fills,
        quotes=closing.quotes + (cq,),
    )
    report = build_daily_report(DAY, opening, closing)
    assert {row.quote_currency: row.daily_pnl_quote for row in report.instruments} == {
        "JPY": Decimal("15.8"),
        "USD": Decimal("1.4"),
    }
    empty = replace(opening, owners=(), fills=(), quotes=())
    finish = replace(empty, checkpoint_id="empty-close", captured_at=END)
    result = build_daily_report(DAY, empty, finish)
    assert result.state == "COMPLETE_ACCOUNTING"
    assert not result.instruments


def test_daily_api_captures_server_sources_publishes_and_restarts(tmp_path):
    import json
    from fastapi.testclient import TestClient
    from pydantic import TypeAdapter
    from ai_quant_os.api import create_app
    from ai_quant_os.contracts import TradeIntent

    now = [START - timedelta(hours=2)]
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="daily-test", paper_db_path=path, clock=lambda: now[0])
    )
    headers = {"X-Operator-Token": "daily-test"}
    capture = "/api/v1/paper/evidence/checkpoints"
    publish = "/api/v1/paper/evidence/daily"
    assert client.post(capture, json={"checkpoint_id": "unauthorized"}).status_code == 401
    assert (
        client.post(
            capture, headers=headers, json={"checkpoint_id": "bad", "owners": []}
        ).status_code
        == 422
    )

    def quote(price):
        response = client.post(
            "/api/v1/paper/quotes",
            headers=headers,
            json={
                "instrument_id": "EURUSD",
                "bid": str(price),
                "ask": str(price + Decimal("0.0002")),
                "observed_at": now[0].isoformat(),
                "world_state_version": "daily-world",
            },
        )
        assert response.status_code == 200, response.text

    def trade(identifier, side, quantity, price, fee):
        quote(price)
        intent = TradeIntent(
            identifier,
            identifier,
            "1",
            "EURUSD",
            side,
            quantity,
            Decimal(10),
            Decimal("0.001"),
            Decimal("0.001"),
            END + timedelta(hours=1),
            "daily-world",
            (),
            now[0],
        )
        order = client.post(
            "/api/v1/paper/intents",
            headers={**headers, "Idempotency-Key": identifier},
            json=json.loads(TypeAdapter(TradeIntent).dump_json(intent)),
        )
        assert order.status_code == 200, order.text
        filled = client.post(
            "/api/v1/paper/fills",
            headers=headers,
            json={
                "fill_id": identifier + "-fill",
                "order_id": order.json()["order_id"],
                "quantity": str(quantity),
                "price": str(price),
                "fee": str(fee),
                "observed_at": now[0].isoformat(),
            },
        )
        assert filled.status_code == 200, filled.text

    trade("before", "BUY", Decimal(10), Decimal(1), Decimal("0.1"))
    now[0] = START
    quote(Decimal("1.1"))
    opening = client.post(capture, headers=headers, json={"checkpoint_id": "api-open"})
    assert opening.status_code == 200, opening.text
    now[0] = START + timedelta(hours=2)
    trade("during", "SELL", Decimal(4), Decimal("1.2"), Decimal("0.2"))
    assert (
        client.post(capture, headers=headers, json={"checkpoint_id": "api-open"}).json()
        == opening.json()
    )
    now[0] = END
    quote(Decimal("1.3"))
    closing = client.post(capture, headers=headers, json={"checkpoint_id": "api-close"})
    assert closing.status_code == 200, closing.text
    request = {
        "day": DAY.isoformat(),
        "opening_checkpoint_id": "api-open",
        "closing_checkpoint_id": "api-close",
    }
    result = client.post(publish, headers=headers, json=request)
    assert result.status_code == 200, result.text
    assert Decimal(result.json()["report"]["instruments"][0]["daily_pnl_quote"]) == Decimal("1.4")
    assert client.post(publish, headers=headers, json=request).json() == result.json()
    restarted = TestClient(
        create_app(operator_token="daily-test", paper_db_path=path, clock=lambda: END)
    )
    assert restarted.get(publish + "/" + DAY.isoformat(), headers=headers).json() == result.json()
    assert restarted.get(publish + "/" + DAY.isoformat()).status_code == 401
    with sqlite3.connect(path.with_name("paper.evidence.sqlite3")) as db:
        db.execute(
            "UPDATE evidence_checkpoints SET blob=? WHERE checkpoint_id='api-open'", (b"{}",)
        )
    assert restarted.get(publish + "/" + DAY.isoformat(), headers=headers).status_code == 503


def test_daily_publication_needs_persistence_and_matching_stored_sources(tmp_path):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.evidence_store import EvidenceStore

    client = TestClient(create_app(operator_token="daily-test"))
    assert (
        client.post(
            "/api/v1/paper/evidence/checkpoints",
            headers={"X-Operator-Token": "daily-test"},
            json={"checkpoint_id": "no-storage"},
        ).status_code
        == 503
    )
    opening, closing = checkpoints()
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    with pytest.raises(ValueError, match="stored checkpoints"):
        store.publish(
            DailyEvidence(opening, closing, build_daily_report(DAY, opening, closing), END)
        )


def test_saved_daily_evidence_replays():
    from pydantic import TypeAdapter
    from ai_quant_os.daily_evidence import DailyEvidence
    from ai_quant_os.evidence_store import encode_evidence

    raw = (Path(__file__).parents[1] / "docs/evaluations/daily-paper-baseline.json").read_bytes()
    evidence = TypeAdapter(DailyEvidence).validate_json(raw)
    assert evidence.report.state == "COMPLETE_ACCOUNTING"
    assert encode_evidence(evidence) == raw

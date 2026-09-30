from datetime import datetime, timedelta, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"X-Operator-Token": "fixture-token"}


def quote(client, instrument, bid, ask, observed_at=NOW):
    response = client.post(
        "/api/v1/paper/quotes",
        headers=HEADERS,
        json={
            "instrument_id": instrument,
            "bid": bid,
            "ask": ask,
            "observed_at": observed_at.isoformat(),
            "world_state_version": "multi-1",
        },
    )
    assert response.status_code == 200


def order(client, key, instrument, quantity):
    return client.post(
        "/api/v1/paper/intents",
        headers={**HEADERS, "Idempotency-Key": key},
        json={
            "intent_id": key,
            "strategy_id": "fixture",
            "strategy_version": "1",
            "instrument_id": instrument,
            "side": "BUY",
            "desired_exposure": quantity,
            "risk_budget": "10",
            "max_spread": "0.1",
            "max_slippage": "0.0002",
            "created_at": NOW.isoformat(),
            "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
            "world_state_version": "multi-1",
            "evidence_refs": ["fixture:multi"],
        },
    )


def test_instrument_unit_limits_are_separate_while_usd_exposure_is_shared():
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    quote(client, "EURUSD", "1.1", "1.1001")
    quote(client, "GBPUSD", "1.3", "1.3001")
    first = order(client, "eur", "EURUSD", "900")
    second = order(client, "gbp", "GBPUSD", "700")
    assert first.json()["status"] == second.json()["status"] == "ACCEPTED"
    assert second.json()["instrument_id"] == "GBPUSD"
    assert client.get("/api/v1/risk?instrument=EURUSD").json()["pending_buy_quantity"] == "900"
    assert client.get("/api/v1/risk?instrument=GBPUSD").json()["pending_buy_quantity"] == "700"
    over_cap = order(client, "extra", "GBPUSD", "100")
    assert over_cap.json()["status"] == "REJECT"
    assert len(client.get("/api/v1/orders").json()["orders"]) == 2


def test_shared_base_currency_cap_blocks_orders_in_different_pairs():
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    quote(client, "EURUSD", "1.1", "1.1001")
    quote(client, "USDJPY", "150", "150.01")
    quote(client, "EURJPY", "165", "165.01")
    assert order(client, "eur-usd", "EURUSD", "1000").json()["status"] == "ACCEPTED"
    assert order(client, "eur-jpy", "EURJPY", "900").json()["status"] == "REJECT"
    assert client.get("/api/v1/risk?instrument=EURJPY").json()["pending_buy_quantity"] == "0"
    assert "CURRENCY_EXPOSURE_LIMIT" in client.get("/api/v1/audit").json()["events"][-1]["reason"]


def test_multi_instrument_fills_reconcile_and_mismatch_blocks_recovery(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(
            operator_token="fixture-token",
            recovery_token="recovery-token",
            clock=lambda: NOW,
            paper_db_path=path,
        )
    )
    quote(client, "EURUSD", "1.1", "1.1001")
    quote(client, "GBPUSD", "1.3", "1.3001")
    for key, instrument, price in (("eur", "EURUSD", "1.1"), ("gbp", "GBPUSD", "1.3")):
        receipt = order(client, key, instrument, "100").json()
        assert receipt["status"] == "ACCEPTED"
        payload = {
            "fill_id": f"fill-{key}",
            "order_id": receipt["order_id"],
            "quantity": "100",
            "price": price,
            "fee": "0.1",
            "observed_at": NOW.isoformat(),
        }
        assert (
            client.post("/api/v1/paper/broker-fills", headers=HEADERS, json=payload).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/paper/orders/{receipt['order_id']}/sync-fills", headers=HEADERS
            ).status_code
            == 200
        )
    result = client.post("/api/v1/paper/positions/reconcile", headers=HEADERS)
    assert result.status_code == 200
    assert result.json()["matches"] is True
    assert {item["instrument_id"] for item in result.json()["instruments"]} == {"EURUSD", "GBPUSD"}
    gbp = client.get("/api/v1/portfolio/snapshot?instrument=GBPUSD").json()
    assert gbp["instrument_id"] == "GBPUSD"
    assert Decimal(gbp["cash_flow_quote"]) == Decimal("-130.1")
    manual = {**payload, "fill_id": "manual-extra", "quantity": "1"}
    partial = order(client, "gbp-more", "GBPUSD", "10").json()
    manual["order_id"] = partial["order_id"]
    assert client.post("/api/v1/paper/fills", headers=HEADERS, json=manual).status_code == 200
    assert (
        client.post("/api/v1/paper/positions/reconcile", headers=HEADERS).json()["matches"] is False
    )
    assert (
        client.post(
            "/api/v1/control/recover-paper",
            headers={**HEADERS, "X-Recovery-Token": "recovery-token", "Idempotency-Key": "recover"},
            json={"reason": "attempt while GBP mismatch exists"},
        ).status_code
        == 409
    )
    restarted = TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, paper_db_path=path)
    )
    assert restarted.get("/api/v1/risk").json()["safety_state"] == "NO_NEW_RISK"
    assert len(restarted.get("/api/v1/fills").json()["fills"]) == 3


def test_required_cross_conversions_and_stale_held_instrument_block_new_risk():
    time = [NOW]
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: time[0]))
    quote(client, "EURJPY", "165", "165.01")
    assert order(client, "missing-fx", "EURJPY", "100").json()["status"] == "REJECT"
    assert "MISSING_FX_RATE" in client.get("/api/v1/audit").json()["events"][-1]["reason"]
    quote(client, "EURUSD", "1.1", "1.1001")
    quote(client, "USDJPY", "150", "150.01")
    assert order(client, "cross", "EURJPY", "100").json()["status"] == "ACCEPTED"
    time[0] = NOW + timedelta(seconds=6)
    quote(client, "GBPUSD", "1.3", "1.3001", observed_at=time[0])
    assert order(client, "stale-held", "GBPUSD", "100").json()["status"] == "REJECT"
    assert "STALE_FX_RATE" in client.get("/api/v1/audit").json()["events"][-1]["reason"]


def test_concurrent_different_instrument_requests_can_retry_without_duplicate_orders():
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    quote(client, "EURUSD", "1.1", "1.1001")
    quote(client, "GBPUSD", "1.3", "1.3001")
    requests = (("eur", "EURUSD", "900"), ("gbp", "GBPUSD", "700"))
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda request: order(client, *request), requests))
    assert all(response.status_code in {200, 409} for response in responses)
    for request in requests:
        retried = order(client, *request)
        assert retried.status_code == 200
        assert retried.json()["status"] == "ACCEPTED"
    assert len(client.get("/api/v1/orders").json()["orders"]) == 2


def test_restart_does_not_hide_unknown_order_behind_complete_fills(tmp_path):
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, paper_db_path=path)
    )
    quote(client, "GBPUSD", "1.3", "1.3001")
    receipt = order(client, "gbp", "GBPUSD", "100").json()
    payload = {
        "fill_id": "complete",
        "order_id": receipt["order_id"],
        "quantity": "100",
        "price": "1.3",
        "fee": "0.1",
        "observed_at": NOW.isoformat(),
    }
    assert (
        client.post("/api/v1/paper/broker-fills", headers=HEADERS, json=payload).status_code == 200
    )
    assert (
        client.post(
            f"/api/v1/paper/orders/{receipt['order_id']}/sync-fills", headers=HEADERS
        ).status_code
        == 200
    )
    with sqlite3.connect(path) as connection:
        raw = connection.execute(
            "SELECT order_json FROM paper_orders WHERE order_id = ?", (receipt["order_id"],)
        ).fetchone()[0]
        interrupted = {**json.loads(raw), "status": "UNKNOWN", "broker_order_id": None}
        connection.execute(
            "UPDATE paper_orders SET order_json = ? WHERE order_id = ?",
            (json.dumps(interrupted), receipt["order_id"]),
        )
    restarted = TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, paper_db_path=path)
    )
    assert restarted.get("/api/v1/risk").json()["safety_state"] == "NO_NEW_RISK"
    assert restarted.get("/api/v1/orders").json()["orders"][0]["status"] == "UNKNOWN"


def test_catalog_and_positions_keep_jpy_cash_flow_in_quote_currency():
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    catalog = client.get("/api/v1/instruments").json()
    assert catalog["mode"] == "PAPER"
    assert {item["instrument_id"] for item in catalog["instruments"]} == {
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "EURJPY",
    }
    quote(client, "USDJPY", "150", "150.01")
    receipt = order(client, "jpy", "USDJPY", "100").json()
    assert receipt["status"] == "ACCEPTED"
    assert (
        client.post(
            "/api/v1/paper/fills",
            headers=HEADERS,
            json={
                "fill_id": "jpy-fill",
                "order_id": receipt["order_id"],
                "quantity": "100",
                "price": "150",
                "fee": "1",
                "observed_at": NOW.isoformat(),
            },
        ).status_code
        == 200
    )
    positions = client.get("/api/v1/portfolio/positions").json()
    assert positions["schema_version"] == "1.0"
    assert len(positions["positions"]) == 4
    jpy = next(row for row in positions["positions"] if row["instrument_id"] == "USDJPY")
    assert (jpy["base_currency"], jpy["quote_currency"]) == ("USD", "JPY")
    assert Decimal(jpy["cash_flow_quote"]) == Decimal("-15001")
    assert Decimal(jpy["marked_pnl_quote"]) == Decimal("-1")
    assert client.get("/api/v1/portfolio/snapshot?instrument=OTHER").status_code == 404
    assert client.get("/api/v1/risk?instrument=OTHER").status_code == 404

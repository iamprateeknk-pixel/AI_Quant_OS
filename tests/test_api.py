from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import IntentInput, QuoteInput, create_app
from ai_quant_os.contracts import TradeIntent
from ai_quant_os.execution import PaperExecutionGateway
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.risk import RiskKernel, RiskLimits
from ai_quant_os.risk import SafetyState


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_retry_dispatches_committed_pending_order_after_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    payload = {
        "intent_id": "pending-intent",
        "strategy_id": "demo",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "1000",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world-1",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world-1",
    }
    trade = TradeIntent(**IntentInput.model_validate(payload).model_dump())
    market = MarketSnapshot(**QuoteInput.model_validate(quote).model_dump(), trusted=True)
    risk = RiskKernel(RiskLimits(Decimal("1000"), Decimal("1500"), 5))
    gateway = PaperExecutionGateway(risk, store_path=path)
    with patch.object(gateway._store, "transition", side_effect=RuntimeError("interrupted")):
        with pytest.raises(RuntimeError, match="interrupted"):
            gateway.submit(
                trade,
                risk.evaluate(trade, market, now=NOW),
                idempotency_key="pending",
                now=NOW,
            )
    client = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "pending"}
    assert client.post("/api/v1/paper/intents", json=payload, headers=headers).status_code == 409
    client.post("/api/v1/paper/quotes", json=quote, headers=headers)
    recovered = client.post("/api/v1/paper/intents", json=payload, headers=headers)
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "ACCEPTED"
    assert recovered.json()["broker_order_id"].startswith("PAPER:")
    assert (
        client.post("/api/v1/paper/intents", json=payload, headers=headers).json()
        == recovered.json()
    )
    assert client.get("/api/v1/risk").json()["pending_buy_quantity"] == "1000"


def test_reconcile_endpoint_uses_durable_ack_without_resending(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    payload = {
        "intent_id": "reconcile-intent",
        "strategy_id": "demo",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "100",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world-1",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world-1",
    }
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "reconcile"}
    client = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    client.post("/api/v1/paper/quotes", json=quote, headers=headers)
    first = client.post("/api/v1/paper/intents", json=payload, headers=headers).json()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT order_json FROM paper_orders WHERE order_id = ?", (first["order_id"],)
        ).fetchone()
        interrupted = json.loads(row[0])
        interrupted["status"] = "UNKNOWN"
        interrupted["broker_order_id"] = None
        connection.execute(
            "UPDATE paper_orders SET order_json = ? WHERE order_id = ?",
            (json.dumps(interrupted, sort_keys=True), first["order_id"]),
        )
    restarted = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    url = f"/api/v1/paper/orders/{first['order_id']}/reconcile"
    assert restarted.post(url).status_code == 401
    result = restarted.post(url, headers={"X-Operator-Token": "test-secret"})
    assert result.status_code == 200
    assert result.json()["status"] == "ACCEPTED"
    assert result.json()["broker_order_id"] == first["broker_order_id"]
    assert restarted.get("/api/v1/risk").json()["safety_state"] == "NO_NEW_RISK"
    after_reconciliation_restart = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    assert after_reconciliation_restart.get("/api/v1/risk").json()["safety_state"] == "NO_NEW_RISK"
    with sqlite3.connect(path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM simulated_broker_orders").fetchone()[0]
    assert count == 1


def test_order_uncertainty_cannot_downgrade_persisted_halt(tmp_path) -> None:
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    store.record_control(
        "halt",
        SafetyState.HALT,
        "operator halt",
        {"receipt_id": "halt-1", "status": "COMPLETED"},
    )
    store.record_order_uncertainty("order-1")
    assert PaperStateStore(tmp_path / "paper.sqlite3").safety_state() == SafetyState.HALT


def test_operator_syncs_durable_simulated_broker_fill(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "broker-sync"}
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world-1",
    }
    trade = {
        "intent_id": "broker-sync",
        "strategy_id": "demo",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "100",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world-1",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }
    client.post("/api/v1/paper/quotes", json=quote, headers=headers)
    order = client.post("/api/v1/paper/intents", json=trade, headers=headers).json()
    payload = {
        "fill_id": "broker-fill-1",
        "order_id": order["order_id"],
        "quantity": "40",
        "price": "1.10",
        "fee": "0.10",
        "observed_at": NOW.isoformat(),
    }
    assert client.post("/api/v1/paper/broker-fills", json=payload).status_code == 401
    recorded = client.post("/api/v1/paper/broker-fills", json=payload, headers=headers)
    assert recorded.status_code == 200
    assert recorded.json()["source"] == "PAPER_BROKER"
    assert client.get("/api/v1/fills").json()["fills"] == []
    url = f"/api/v1/paper/orders/{order['order_id']}/sync-fills"
    assert client.post(url).status_code == 401
    assert client.post(url, headers=headers).json()["imported_count"] == 1
    assert client.post(url, headers=headers).json()["imported_count"] == 0
    restarted = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    assert restarted.get("/api/v1/fills").json()["fills"][0]["source"] == "PAPER_BROKER"
    assert restarted.get("/api/v1/orders").json()["orders"][0]["status"] == "PARTIALLY_FILLED"
    reconcile_url = "/api/v1/paper/positions/reconcile"
    assert restarted.post(reconcile_url).status_code == 401
    matched = restarted.post(reconcile_url, headers=headers)
    assert matched.status_code == 200
    assert matched.json()["matches"] is True
    assert matched.json()["local_net_quantity"] == "40"
    assert restarted.get("/api/v1/risk").json()["safety_state"] == "NORMAL"

    manual = {**payload, "fill_id": "manual-extra", "quantity": "10"}
    assert restarted.post("/api/v1/paper/fills", json=manual, headers=headers).status_code == 200
    mismatch = restarted.post(reconcile_url, headers=headers)
    assert mismatch.status_code == 200
    assert mismatch.json()["matches"] is False
    assert mismatch.json()["local_only_fill_ids"] == ["manual-extra"]
    assert mismatch.json()["local_net_quantity"] == "50"
    assert mismatch.json()["broker_net_quantity"] == "40"
    after_mismatch_restart = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    assert after_mismatch_restart.get("/api/v1/risk").json()["safety_state"] == "NO_NEW_RISK"


def test_status_is_paper_only_and_starts_with_no_orders() -> None:
    client = TestClient(create_app(operator_token="test-secret"))
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    assert response.json()["mode"] == "PAPER"
    assert response.json()["order_count"] == 0


def test_broker_read_model_reports_only_simulated_paper_route() -> None:
    client = TestClient(create_app(operator_token="test-secret"))
    response = client.get("/api/v1/brokers")
    assert response.status_code == 200
    assert response.json() == {
        "brokers": [
            {
                "broker_id": "PAPER",
                "connection_state": "CONNECTED",
                "enabled": True,
                "instruments": ["EURUSD"],
            }
        ]
    }


def test_market_read_model_reports_simulated_quote_freshness() -> None:
    client = TestClient(create_app(operator_token="test-secret", clock=lambda: NOW))
    assert client.get("/api/v1/markets/EURUSD").status_code == 404
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world-1",
    }
    client.post("/api/v1/paper/quotes", json=quote, headers={"X-Operator-Token": "test-secret"})
    response = client.get("/api/v1/markets/EURUSD")
    assert response.status_code == 200
    assert response.json()["source"] == "PAPER_SIMULATED"
    assert response.json()["freshness"] == "FRESH"
    assert response.json()["bid"] == "1.1000"


def test_command_denied_without_configured_operator_token() -> None:
    client = TestClient(create_app(operator_token=""))
    response = client.post(
        "/api/v1/control/no-new-risk",
        json={"reason": "test"},
        headers={"Idempotency-Key": "command-1"},
    )
    assert response.status_code == 503


def test_command_denied_for_wrong_token() -> None:
    client = TestClient(create_app(operator_token="test-secret"))
    response = client.post(
        "/api/v1/control/no-new-risk",
        json={"reason": "test"},
        headers={"Idempotency-Key": "command-1"},
    )
    assert response.status_code == 401


def test_intent_payload_cannot_override_risk_limit() -> None:
    client = TestClient(create_app(operator_token="test-secret", clock=lambda: NOW))
    response = client.post(
        "/api/v1/paper/intents",
        json={
            "intent_id": "override",
            "strategy_id": "demo",
            "strategy_version": "1",
            "instrument_id": "EURUSD",
            "side": "BUY",
            "desired_exposure": "10000",
            "risk_budget": "10",
            "max_spread": "0.0003",
            "max_slippage": "0.0002",
            "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
            "world_state_version": "world-1",
            "evidence_refs": ["fixture:1"],
            "created_at": NOW.isoformat(),
            "max_order_exposure": "1000000",
        },
        headers={"X-Operator-Token": "test-secret", "Idempotency-Key": "override"},
    )
    assert response.status_code == 422


def test_paper_intent_is_risk_checked_and_idempotent() -> None:
    client = TestClient(create_app(operator_token="test-secret", clock=lambda: NOW))
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "one"}
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world-1",
    }
    intent = {
        "intent_id": "intent-1",
        "strategy_id": "demo",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "100",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world-1",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }
    assert client.post("/api/v1/paper/quotes", json=quote, headers=headers).status_code == 200
    first = client.post("/api/v1/paper/intents", json=intent, headers=headers)
    second = client.post("/api/v1/paper/intents", json=intent, headers=headers)
    assert first.status_code == 200
    assert first.json()["order_id"] == second.json()["order_id"]
    assert client.get("/api/v1/orders").json()["orders"] == [first.json()]
    assert len(client.get("/api/v1/audit").json()["events"]) == 1


def test_halt_blocks_new_paper_order() -> None:
    client = TestClient(create_app(operator_token="test-secret", clock=lambda: NOW))
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "halt-1"}
    response = client.post(
        "/api/v1/control/halt", json={"reason": "operator drill"}, headers=headers
    )
    assert response.status_code == 200
    assert client.get("/api/v1/risk").json()["safety_state"] == "HALT"


def test_repeated_control_returns_same_receipt_without_duplicate_audit() -> None:
    client = TestClient(create_app(operator_token="test-secret", clock=lambda: NOW))
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "control-1"}
    first = client.post(
        "/api/v1/control/no-new-risk", json={"reason": "feed loss"}, headers=headers
    )
    second = client.post(
        "/api/v1/control/no-new-risk", json={"reason": "feed loss"}, headers=headers
    )
    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["receipt_id"]
    assert len(client.get("/api/v1/audit").json()["events"]) == 1


def test_changed_control_payload_cannot_reuse_key() -> None:
    client = TestClient(create_app(operator_token="test-secret", clock=lambda: NOW))
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "control-1"}
    client.post("/api/v1/control/no-new-risk", json={"reason": "feed loss"}, headers=headers)
    response = client.post("/api/v1/control/halt", json={"reason": "new reason"}, headers=headers)
    assert response.status_code == 409


def test_api_retry_after_restart_returns_original_receipt_without_quote(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "restart-key"}
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world-1",
    }
    intent = {
        "intent_id": "intent-1",
        "strategy_id": "demo",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "100",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world-1",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }
    first_client = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    first_client.post("/api/v1/paper/quotes", json=quote, headers=headers)
    first = first_client.post("/api/v1/paper/intents", json=intent, headers=headers)

    restarted = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    second = restarted.post("/api/v1/paper/intents", json=intent, headers=headers)
    assert second.status_code == 200
    assert second.json() == first.json()
    assert restarted.get("/api/v1/orders").json()["orders"] == [first.json()]


def test_halt_and_control_receipt_survive_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "halt-key"}
    first = TestClient(create_app(operator_token="test-secret", paper_db_path=path))
    receipt = first.post(
        "/api/v1/control/halt", json={"reason": "broker mismatch"}, headers=headers
    )
    assert receipt.status_code == 200

    restarted = TestClient(create_app(operator_token="test-secret", paper_db_path=path))
    assert restarted.get("/api/v1/risk").json()["safety_state"] == "HALT"
    retry = restarted.post(
        "/api/v1/control/halt", json={"reason": "broker mismatch"}, headers=headers
    )
    assert retry.status_code == 200
    assert retry.json() == receipt.json()
    assert restarted.get("/api/v1/audit").json()["events"] == [
        {
            "action": "HALT",
            "intent_id": "",
            "reason": "broker mismatch",
            "trace_id": receipt.json()["receipt_id"],
        }
    ]


def test_restarted_halt_cannot_be_lowered_by_new_control(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = TestClient(create_app(operator_token="test-secret", paper_db_path=path))
    first.post(
        "/api/v1/control/halt",
        json={"reason": "incident"},
        headers={"X-Operator-Token": "test-secret", "Idempotency-Key": "halt"},
    )
    restarted = TestClient(create_app(operator_token="test-secret", paper_db_path=path))
    response = restarted.post(
        "/api/v1/control/no-new-risk",
        json={"reason": "try lowering"},
        headers={"X-Operator-Token": "test-secret", "Idempotency-Key": "lower"},
    )
    assert response.status_code == 409
    assert restarted.get("/api/v1/risk").json()["safety_state"] == "HALT"


def test_paper_order_audit_survives_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "order-audit"}
    client = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world-1",
    }
    payload = {
        "intent_id": "audit-intent",
        "strategy_id": "demo",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "100",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world-1",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }
    client.post("/api/v1/paper/quotes", json=quote, headers=headers)
    assert client.post("/api/v1/paper/intents", json=payload, headers=headers).status_code == 200

    restarted = TestClient(create_app(operator_token="test-secret", paper_db_path=path))
    events = restarted.get("/api/v1/audit").json()["events"]
    assert events == [{"action": "PAPER_ORDER", "intent_id": "audit-intent", "reason": "APPROVE"}]


def test_paper_fill_and_portfolio_snapshot_survive_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    headers = {"X-Operator-Token": "test-secret", "Idempotency-Key": "portfolio-order"}
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1500",
        "ask": "1.1501",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world-1",
    }
    trade = {
        "intent_id": "portfolio-intent",
        "strategy_id": "demo",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "100",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world-1",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }
    client = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    client.post("/api/v1/paper/quotes", json=quote, headers=headers)
    order = client.post("/api/v1/paper/intents", json=trade, headers=headers).json()
    payload = {
        "fill_id": "portfolio-fill",
        "order_id": order["order_id"],
        "quantity": "100",
        "price": "1.10",
        "fee": "0.10",
        "observed_at": NOW.isoformat(),
    }
    first = client.post("/api/v1/paper/fills", json=payload, headers=headers)
    retry = client.post("/api/v1/paper/fills", json=payload, headers=headers)
    assert first.status_code == 200
    assert retry.json() == first.json()
    marked = client.get("/api/v1/portfolio/snapshot").json()
    assert marked["net_quantity"] == "100"
    assert marked["mark_price"] == "1.1500"
    assert marked["marked_pnl_quote"] == "4.9000"
    assert client.get("/api/v1/orders").json()["orders"][0]["status"] == "FILLED"

    restarted = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    unmarked = restarted.get("/api/v1/portfolio/snapshot").json()
    assert unmarked["net_quantity"] == "100"
    assert unmarked["mark_price"] is None
    assert unmarked["marked_pnl_quote"] is None
    assert len(restarted.get("/api/v1/fills").json()["fills"]) == 1
    assert restarted.get("/api/v1/orders").json()["orders"][0]["status"] == "FILLED"
    assert [event["action"] for event in restarted.get("/api/v1/audit").json()["events"]] == [
        "PAPER_ORDER",
        "PAPER_FILL",
    ]

    stale = TestClient(
        create_app(
            operator_token="test-secret",
            clock=lambda: NOW + timedelta(seconds=10),
            paper_db_path=path,
        )
    )
    stale.post("/api/v1/paper/quotes", json=quote, headers=headers)
    assert stale.get("/api/v1/portfolio/snapshot").json()["marked_pnl_quote"] is None


def test_paper_fill_requires_operator_and_known_order(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    payload = {
        "fill_id": "unknown",
        "order_id": "not-an-order",
        "quantity": "1",
        "price": "1.10",
        "fee": "0",
        "observed_at": NOW.isoformat(),
    }
    assert client.post("/api/v1/paper/fills", json=payload).status_code == 401
    assert (
        client.post(
            "/api/v1/paper/fills",
            json=payload,
            headers={"X-Operator-Token": "test-secret"},
        ).status_code
        == 409
    )


def test_api_portfolio_limit_rejects_new_buy_but_allows_risk_reducing_sell(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    client = TestClient(
        create_app(operator_token="test-secret", clock=lambda: NOW, paper_db_path=path)
    )
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": NOW.isoformat(),
        "world_state_version": "world-1",
    }
    client.post("/api/v1/paper/quotes", json=quote, headers={"X-Operator-Token": "test-secret"})
    base_intent = {
        "intent_id": "first",
        "strategy_id": "demo",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "1000",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "world-1",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }

    def submit(payload: dict[str, str], key: str):
        return client.post(
            "/api/v1/paper/intents",
            json=payload,
            headers={"X-Operator-Token": "test-secret", "Idempotency-Key": key},
        )

    first = submit(base_intent, "first")
    assert first.status_code == 200
    assert first.json()["status"] == "ACCEPTED"
    observed_risk = client.get("/api/v1/risk").json()
    assert observed_risk["max_portfolio_exposure"] == "1500"
    assert observed_risk["worst_case_quantity"] == "1000"
    assert observed_risk["pending_buy_quantity"] == "1000"
    assert observed_risk["portfolio_snapshot_id"] != "paper:empty"
    excess = submit({**base_intent, "intent_id": "excess", "desired_exposure": "600"}, "excess")
    assert excess.status_code == 200
    assert excess.json()["status"] == "REJECT"
    assert "PORTFOLIO_EXPOSURE_LIMIT" in client.get("/api/v1/audit").json()["events"][-1]["reason"]
    reduce = submit(
        {**base_intent, "intent_id": "reduce", "side": "SELL", "desired_exposure": "600"},
        "reduce",
    )
    assert reduce.status_code == 200
    assert reduce.json()["status"] == "ACCEPTED"

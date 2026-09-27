from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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

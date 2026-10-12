import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from ai_quant_os.api import create_app
from test_broker_router import NOW


def test_broker_health_is_protected_and_does_not_invent_missing_metrics():
    path = "/api/v1/brokers/health"
    with TestClient(create_app(operator_token="operator", clock=lambda: NOW)) as client:
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"X-Operator-Token": "operator"})

    assert response.status_code == 200
    value = response.json()
    assert value["schema_version"] == "1.0"
    assert value["mode"] == "PAPER"
    assert value["live_execution_released"] is False
    assert len(value["brokers"]) == 5
    rows = {row["broker_id"]: row for row in value["brokers"]}
    assert rows["PAPER"]["connection_state"] == "CONNECTED"
    assert all(
        rows[identity]["connection_state"] == "UNKNOWN"
        for identity in {"EXNESS_MT5_DEMO", "EXNESS", "IBKR", "ZERODHA"}
    )
    for row in rows.values():
        assert row["quote_age_ms"] is None
        assert row["order_latency_ms"] is None
        assert row["reject_rate"] is None
        assert row["reconciliation_state"] == "UNASSESSED"
        assert row["spread_state"] == "UNASSESSED"
        assert row["account_state"] == "UNASSESSED"
        assert row["health_score"] is None
        assert row["updated_at"] == "2026-01-01T00:00:00Z"


def test_broker_health_schema_matches_model_and_rejects_claimed_release():
    from ai_quant_os.broker_capability_api import BrokerHealthRegistry

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "docs/schemas/broker-health.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema == BrokerHealthRegistry.model_json_schema()
    Draft202012Validator.check_schema(schema)
    with TestClient(create_app(operator_token="operator", clock=lambda: NOW)) as client:
        value = client.get(
            "/api/v1/brokers/health", headers={"X-Operator-Token": "operator"}
        ).json()
    value["live_execution_released"] = True
    assert not Draft202012Validator(schema).is_valid(value)

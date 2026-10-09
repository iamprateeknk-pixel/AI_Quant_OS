import json
from pathlib import Path

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_broker_router import NOW


def test_capabilities_are_protected_and_distinguish_paper_demo_and_skeletons():
    with TestClient(create_app(operator_token="operator", clock=lambda: NOW)) as client:
        path = "/api/v1/brokers/capabilities"
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"X-Operator-Token": "operator"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        value = response.json()
        assert value["live_execution_released"] is False
        rows = {row["broker_id"]: row for row in value["brokers"]}
        assert set(rows) == {"PAPER", "EXNESS_MT5_DEMO", "EXNESS", "IBKR", "ZERODHA"}
        assert rows["PAPER"]["paper_admission_policy_allows"] is True
        assert rows["PAPER"]["compliance_scope"] == "LOCAL_SIMULATION_ONLY"
        assert rows["EXNESS_MT5_DEMO"]["implementation"] == "DEMO_OBSERVATION_LIBRARY"
        assert "ACCOUNT_READ" in rows["EXNESS_MT5_DEMO"]["operations"]
        assert rows["EXNESS_MT5_DEMO"]["bound_to_execution_router"] is False
        for identity in ["EXNESS", "IBKR", "ZERODHA"]:
            assert rows[identity]["implementation"] == "DISABLED_SKELETON"
            assert rows[identity]["operations"] == []
        for identity, row in rows.items():
            if identity != "PAPER":
                assert row["eligible"] is False and row["compliance_scope"] == "UNVERIFIED"
                assert row["paper_admission_policy_allows"] is False
        assert client.post(path, headers={"X-Operator-Token": "operator"}).status_code == 405


def test_capability_schema_matches_runtime_and_denies_live_release():
    import pytest
    from jsonschema import Draft202012Validator, ValidationError
    from ai_quant_os.broker_capability_api import BrokerCapabilityRegistry

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "docs/schemas/broker-capability-registry.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema == BrokerCapabilityRegistry.model_json_schema()
    with TestClient(create_app(operator_token="operator", clock=lambda: NOW)) as client:
        value = client.get(
            "/api/v1/brokers/capabilities", headers={"X-Operator-Token": "operator"}
        ).json()
    validator = Draft202012Validator(schema)
    validator.validate(value)
    value["live_execution_released"] = True
    with pytest.raises(ValidationError):
        validator.validate(value)


def test_native_adapter_enablement_does_not_change_capability_release_policy():
    from ai_quant_os.broker import BrokerRouter, SimulatedBroker
    from ai_quant_os.broker_capability_api import capability_registry

    router = BrokerRouter([SimulatedBroker("IBKR", {"EURUSD": "EURUSD"}, enabled=True)])
    registry = capability_registry(router)
    item = next(row for row in registry.brokers if row.broker_id == "IBKR")
    assert item.bound_to_execution_router is True
    assert item.eligible is False and item.paper_admission_policy_allows is False
    assert item.live_execution_released is False


def test_frontend_fixture_matches_actual_default_api_contract():
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "apps/web/tests/fixtures/broker-capabilities.json"
        ).read_text(encoding="utf-8")
    )
    with TestClient(create_app(operator_token="operator", clock=lambda: NOW)) as client:
        actual = client.get(
            "/api/v1/brokers/capabilities", headers={"X-Operator-Token": "operator"}
        ).json()
    assert fixture == actual

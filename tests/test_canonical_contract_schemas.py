import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from pydantic import TypeAdapter

from ai_quant_os.contracts import OrderCommand, RiskDecision


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ID = "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize(
    "name,contract,contract_id,sample",
    [
        (
            "risk-decision-v1",
            RiskDecision,
            "urn:ai-quant-os:contract:risk-decision:1.0",
            {
                "risk_id": "risk-1",
                "intent_id": "intent-1",
                "decision": "APPROVE",
                "approved_exposure": "100.0",
                "rules_evaluated": ["PAPER_ONLY"],
                "violations": [],
                "portfolio_snapshot_id": "portfolio-1",
                "created_at": "2026-10-12T00:00:00Z",
            },
        ),
        (
            "order-command-v1",
            OrderCommand,
            "urn:ai-quant-os:contract:order-command:1.0",
            {
                "order_id": "order-1",
                "idempotency_key": "intent-1",
                "risk_id": "risk-1",
                "broker_route": "PAPER",
                "instrument_id": "EURUSD",
                "order_type": "MARKET",
                "side": "BUY",
                "quantity": "100.0",
                "limit_price": None,
                "stop_price": None,
                "time_in_force": "IOC",
                "expires_at": "2026-10-12T00:01:00Z",
            },
        ),
    ],
)
def test_versioned_internal_contract_schema_matches_runtime(name, contract, contract_id, sample):
    path = ROOT / "docs" / "schemas" / f"{name}.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    expected = TypeAdapter(contract).json_schema()
    expected.update({"$schema": SCHEMA_ID, "$id": contract_id, "additionalProperties": False})
    assert schema == expected
    validator = Draft202012Validator(schema)
    validator.validate(sample)
    with pytest.raises(ValidationError):
        validator.validate({**sample, "unrecognized": True})
    with pytest.raises(TypeError):
        contract(**{**sample, "unrecognized": True})

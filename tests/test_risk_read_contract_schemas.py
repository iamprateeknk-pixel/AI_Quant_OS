import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError

from ai_quant_os.api import create_app


ROOT = Path(__file__).resolve().parents[1]


def schema(name: str) -> dict[str, object]:
    result = json.loads((ROOT / "docs/schemas" / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(result)
    return result


def test_system_status_v1_contract_validates_actual_safety_state_read():
    contract = schema("paper-system-status-v1.schema.json")
    client = TestClient(create_app())
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    payload = response.json()
    Draft202012Validator(contract).validate(payload)
    with pytest.raises(ValidationError):
        Draft202012Validator(contract).validate({**payload, "safety_state": "LIVE"})
    with pytest.raises(ValidationError):
        Draft202012Validator(contract).validate({**payload, "extra": True})


def test_risk_v1_contract_validates_instrument_scoped_exposure_read():
    contract = schema("paper-risk-v1.schema.json")
    client = TestClient(create_app())
    response = client.get("/api/v1/risk?instrument=GBPUSD")
    assert response.status_code == 200
    payload = response.json()
    assert payload["instrument_id"] == "GBPUSD"
    Draft202012Validator(contract).validate(payload)
    with pytest.raises(ValidationError):
        Draft202012Validator(contract).validate({**payload, "safety_state": "LIVE"})
    with pytest.raises(ValidationError):
        Draft202012Validator(contract).validate({**payload, "extra": True})

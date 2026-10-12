import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError

from ai_quant_os.api import create_app


ROOT = Path(__file__).resolve().parents[1]


def load_schema(name: str) -> dict[str, object]:
    schema = json.loads((ROOT / "docs/schemas" / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def test_paper_positions_v1_schema_validates_actual_endpoint_response():
    schema = load_schema("paper-positions-v1.schema.json")
    assert set(schema["properties"]) == {"schema_version", "mode", "positions"}
    assert set(schema["$defs"]["Position"]["properties"]) == {
        "mode",
        "instrument_id",
        "base_currency",
        "quote_currency",
        "mark_available",
        "mark_price",
        "net_quantity",
        "cash_flow_quote",
        "gross_exposure_quote",
        "net_exposure_quote",
        "marked_pnl_quote",
    }
    client = TestClient(create_app())
    response = client.get("/api/v1/portfolio/positions")
    assert response.status_code == 200
    Draft202012Validator(schema).validate(response.json())
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate({**response.json(), "extra": True})


def test_paper_currency_portfolio_v1_schema_validates_actual_endpoint_response():
    schema = load_schema("paper-currency-portfolio-v1.schema.json")
    assert set(schema["properties"]) == {
        "schema_version",
        "snapshot_id",
        "reporting_currency",
        "currencies",
        "marked_pnl_reporting",
        "gross_worst_case_exposure_reporting",
        "issues",
        "valuation_available",
        "mode",
    }
    assert set(schema["$defs"]["CurrencyExposure"]["properties"]) == {
        "currency",
        "filled_balance",
        "minimum_balance",
        "maximum_balance",
        "marked_balance_reporting",
        "worst_case_exposure_reporting",
        "issue",
    }
    client = TestClient(create_app())
    response = client.get("/api/v1/portfolio/currencies")
    assert response.status_code == 200
    Draft202012Validator(schema).validate(response.json())
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate({**response.json(), "extra": True})

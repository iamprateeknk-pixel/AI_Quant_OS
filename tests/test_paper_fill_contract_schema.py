import json
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from ai_quant_os.api import _fill_view
from ai_quant_os.portfolio import PaperFill


ROOT = Path(__file__).resolve().parents[1]


def test_paper_fills_v1_schema_matches_runtime_projection():
    schema = json.loads(
        (ROOT / "docs/schemas/paper-fills-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    item_schema = schema["$defs"]["PaperFill"]
    assert set(item_schema["properties"]) == {field.name for field in fields(PaperFill)}

    fill = PaperFill(
        fill_id="fill-01",
        order_id="order-01",
        instrument_id="EURUSD",
        side="BUY",
        quantity=Decimal("12.50"),
        price=Decimal("1.08002"),
        fee=Decimal("0.01"),
        observed_at=datetime(2026, 10, 13, 10, tzinfo=timezone.utc),
        source="PAPER_BROKER",
    )
    payload = {"fills": [_fill_view(fill)]}
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(payload)
    with pytest.raises(ValidationError):
        validator.validate({**payload, "unknown": "field"})
    with pytest.raises(ValidationError):
        validator.validate({"fills": [{**payload["fills"][0], "side": "SHORT"}]})
    with pytest.raises(ValidationError):
        validator.validate({"fills": [{**payload["fills"][0], "quantity": "0e+10"}]})

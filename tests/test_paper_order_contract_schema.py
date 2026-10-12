import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from ai_quant_os.api import _order_view
from ai_quant_os.contracts import OrderCommand
from ai_quant_os.execution import PaperOrder


ROOT = Path(__file__).resolve().parents[1]


def test_paper_orders_v1_schema_matches_runtime_projection():
    schema = json.loads(
        (ROOT / "docs/schemas/paper-orders-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    item_schema = schema["$defs"]["PaperOrder"]
    assert set(item_schema["properties"]) == {
        "order_id",
        "intent_id",
        "risk_id",
        "status",
        "broker_route",
        "instrument_id",
        "broker_order_id",
    }

    command = OrderCommand(
        order_id="order-01",
        idempotency_key="intent-01",
        risk_id="risk-01",
        broker_route="PAPER",
        instrument_id="EURUSD",
        order_type="MARKET",
        side="BUY",
        quantity=Decimal("12.50"),
        limit_price=None,
        stop_price=None,
        time_in_force="IOC",
        expires_at=datetime(2026, 10, 13, 10, 1, tzinfo=timezone.utc),
    )
    order = PaperOrder("order-01", "intent-01", "risk-01", "ACCEPTED", command, "sim-01")
    payload = {"orders": [_order_view(order)]}
    validator = Draft202012Validator(schema)
    validator.validate(payload)
    with pytest.raises(ValidationError):
        validator.validate({"orders": [{**payload["orders"][0], "status": "LIVE"}]})
    with pytest.raises(ValidationError):
        validator.validate({"orders": [{**payload["orders"][0], "broker_route": "MT5"}]})
    with pytest.raises(ValidationError):
        validator.validate({"orders": [{**payload["orders"][0], "unknown": True}]})

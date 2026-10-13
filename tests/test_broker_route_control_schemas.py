"""Published broker route command and receipt contracts match runtime models."""

import json
from pathlib import Path

import pytest

from ai_quant_os.broker_route_control_api import (
    BrokerRouteControlCommand,
    BrokerRouteControlReceipt,
)


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("broker-route-control-command", BrokerRouteControlCommand),
        ("broker-route-control-receipt", BrokerRouteControlReceipt),
    ],
)
def test_published_route_control_schema_matches_runtime(name, model):
    path = Path(__file__).resolve().parents[1] / "docs/schemas" / f"{name}.schema.json"
    assert path.is_file(), "Published route command and receipt schemas are required"
    assert json.loads(path.read_text(encoding="utf-8")) == model.model_json_schema()

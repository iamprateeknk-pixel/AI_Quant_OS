import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from ai_quant_os.api import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_paper_market_v1_schema_validates_fresh_and_stale_api_responses():
    schema = json.loads(
        (ROOT / "docs/schemas/paper-market-snapshot-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    assert set(schema["properties"]) == {
        "instrument_id",
        "bid",
        "ask",
        "observed_at",
        "world_state_version",
        "trusted",
        "freshness",
        "source",
    }
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    now = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
    current = [now]
    client = TestClient(create_app(operator_token="test-secret", clock=lambda: current[0]))
    quote = {
        "instrument_id": "EURUSD",
        "bid": "1.1000",
        "ask": "1.1001",
        "observed_at": now.isoformat(),
        "world_state_version": "world-1",
    }
    posted = client.post(
        "/api/v1/paper/quotes", json=quote, headers={"X-Operator-Token": "test-secret"}
    )
    assert posted.status_code == 200
    fresh = client.get("/api/v1/markets/EURUSD").json()
    assert fresh["freshness"] == "FRESH"
    validator.validate(fresh)
    with pytest.raises(ValidationError):
        validator.validate({**fresh, "unexpected": True})

    current[0] += timedelta(seconds=6)
    stale = client.get("/api/v1/markets/EURUSD").json()
    assert stale["freshness"] == "STALE"
    validator.validate(stale)

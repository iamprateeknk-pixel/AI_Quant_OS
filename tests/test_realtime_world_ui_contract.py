"""Bind the populated UI sample to the actual protected paper world snapshot."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def populated_frame():
    headers = {"X-Operator-Token": "fixture-token"}
    with TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW)) as client:
        quote = {
            "instrument_id": "EURUSD",
            "bid": "1.102",
            "ask": "1.1021",
            "observed_at": NOW.isoformat(),
            "world_state_version": "realtime-world",
        }
        assert client.post("/api/v1/paper/quotes", headers=headers, json=quote).status_code == 200
        bars = [
            {
                "instrument_id": "EURUSD",
                "observed_at": (NOW - timedelta(seconds=2 - index)).isoformat(),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": "100",
            }
            for index, price in enumerate(("1.1", "1.101", "1.102"))
        ]
        assert (
            client.post(
                "/api/v1/paper/world-state/bars", headers=headers, json={"bars": bars}
            ).status_code
            == 200
        )
        result = client.get("/api/v1/realtime/snapshot", headers=headers)
        assert result.status_code == 200
        return result.json()


def test_populated_realtime_world_fixture_matches_original_api_features_and_coverage():
    actual = populated_frame()
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "apps/web/tests/fixtures/realtime-world.json"
        ).read_text(encoding="utf-8")
    )
    fixture["epoch"] = actual["epoch"]
    assert actual == fixture
    euro = next(row for row in actual["world"]["instruments"] if row["instrument_id"] == "EURUSD")
    assert euro["history_status"] == "FRESH" and euro["quote_status"] == "FRESH"
    assert euro["return_bps"] != "0" and euro["realized_volatility_bps"] is not None

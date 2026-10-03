from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-price-budget"}


@pytest.mark.parametrize("price", ["1e1000000", "1e-1000000", "2." + "0" * 65])
def test_quote_representation_budget_rejects_before_quote_publication(price):
    with TestClient(create_app(operator_token="fixture-price-budget", clock=lambda: NOW)) as client:
        response = client.post(
            "/api/v1/paper/quotes",
            headers=HEADERS,
            json={
                "instrument_id": "EURUSD",
                "bid": price,
                "ask": price,
                "observed_at": NOW.isoformat(),
                "world_state_version": "fixture",
            },
        )
        assert response.status_code == 422
        world = client.get("/api/v1/world-state").json()
        assert (
            next(row for row in world["instruments"] if row["instrument_id"] == "EURUSD")[
                "quote_status"
            ]
            == "MISSING"
        )


def test_unbounded_ohlc_rejects_whole_batch_before_history_changes():
    with TestClient(create_app(operator_token="fixture-price-budget", clock=lambda: NOW)) as client:
        bar = {
            "instrument_id": "EURUSD",
            "observed_at": NOW.isoformat(),
            "open": "2",
            "high": "2",
            "low": "2",
            "close": "2",
            "volume": "1",
        }
        bar["observed_at"] = (NOW - timedelta(seconds=1)).isoformat()
        bad = bar | {"high": "1e1000000", "observed_at": NOW.isoformat()}
        response = client.post(
            "/api/v1/paper/world-state/bars", headers=HEADERS, json={"bars": [bar, bad]}
        )
        assert response.status_code == 422
        world = client.get("/api/v1/world-state").json()
        assert all(row["bar_count"] == 0 for row in world["instruments"])


def test_unknown_quote_instrument_is_not_published():
    with TestClient(create_app(operator_token="fixture-price-budget", clock=lambda: NOW)) as client:
        response = client.post(
            "/api/v1/paper/quotes",
            headers=HEADERS,
            json={
                "instrument_id": "UNKNOWN",
                "bid": "2",
                "ask": "2",
                "observed_at": NOW.isoformat(),
                "world_state_version": "fixture",
            },
        )
        assert response.status_code == 422

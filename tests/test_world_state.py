from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def quote(instrument="EURUSD", age=0, trusted=True):
    return MarketSnapshot(
        instrument,
        Decimal("1.1"),
        Decimal("1.1001"),
        NOW - timedelta(seconds=age),
        "fixture",
        trusted,
    )


def bars(instrument="EURUSD", closes=("1", "1.001", "1.002", "1.003")):
    return tuple(
        Bar(
            instrument,
            NOW - timedelta(seconds=len(closes) - index - 1),
            Decimal(close),
            Decimal(close),
            Decimal(close),
            Decimal(close),
            Decimal(1),
        )
        for index, close in enumerate(closes)
    )


def test_snapshot_is_reproducible_and_includes_currency_coverage():
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    engine.record_bars(bars(), now=NOW)
    snapshot = engine.snapshot((quote(),), now=NOW)
    assert snapshot == engine.snapshot((quote(),), now=NOW)
    assert snapshot.schema_version == "1.0"
    assert snapshot.valid_until == NOW + timedelta(seconds=5)
    assert snapshot.status == "DEGRADED"
    eur = next(item for item in snapshot.instruments if item.instrument_id == "EURUSD")
    assert eur.quote_status == "FRESH"
    assert eur.regime == "TRENDING"
    assert eur.return_bps == Decimal("30")
    assert eur.bar_count == 4
    assert eur.evidence_refs
    twin = next(item for item in snapshot.currency_twins if item.currency == "EUR")
    assert twin.instrument_ids == ("EURJPY", "EURUSD")
    assert twin.fresh_quote_count == 1
    assert twin.macro_available is False


@pytest.mark.parametrize(
    "age,trusted,status", [(6, True, "STALE"), (-1, True, "FUTURE"), (0, False, "UNTRUSTED")]
)
def test_unusable_quotes_do_not_produce_actionable_regimes(age, trusted, status):
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    engine.record_bars(bars(), now=NOW)
    instrument = engine.snapshot((quote(age=age, trusted=trusted),), now=NOW).instruments[0]
    assert instrument.quote_status == status
    assert instrument.regime == "UNKNOWN"


def test_history_is_atomic_bounded_and_rejects_conflicts_and_future_data():
    engine = WorldStateEngine(PAPER_INSTRUMENTS, history_limit=4)
    engine.record_bars(bars(), now=NOW)
    original = engine.snapshot((quote(),), now=NOW)
    assert engine.record_bars(bars(), now=NOW) == 0
    with pytest.raises(ValueError, match="Conflicting"):
        engine.record_bars(bars(closes=("2", "2", "2", "2")), now=NOW)
    assert engine.snapshot((quote(),), now=NOW) == original
    future = bars()[0]
    with pytest.raises(ValueError, match="future"):
        engine.record_bars(
            (
                Bar(
                    future.instrument_id,
                    NOW + timedelta(seconds=1),
                    future.open,
                    future.high,
                    future.low,
                    future.close,
                    future.volume,
                ),
            ),
            now=NOW,
        )
    later = NOW + timedelta(seconds=1)
    engine.record_bars(
        (
            Bar(
                "EURUSD",
                later,
                Decimal("1.004"),
                Decimal("1.004"),
                Decimal("1.004"),
                Decimal("1.004"),
                Decimal(1),
            ),
        ),
        now=later,
    )
    assert engine.snapshot((quote(),), now=later).instruments[0].bar_count == 4


def test_stale_history_and_missing_quotes_remain_unknown():
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    engine.record_bars(bars(), now=NOW)
    snapshot = engine.snapshot((quote(),), now=NOW + timedelta(minutes=2))
    assert snapshot.instruments[0].regime == "UNKNOWN"
    assert snapshot.instruments[0].history_status == "STALE"
    assert engine.snapshot((), now=NOW).status == "EMPTY"


def test_paper_api_has_read_snapshot_and_authenticated_bar_ingestion():
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    empty = client.get("/api/v1/world-state")
    assert empty.status_code == 200
    assert empty.json()["status"] == "EMPTY"
    payload = {
        "bars": [
            {
                "instrument_id": b.instrument_id,
                "observed_at": b.observed_at.isoformat(),
                "open": str(b.open),
                "high": str(b.high),
                "low": str(b.low),
                "close": str(b.close),
                "volume": str(b.volume),
            }
            for b in bars()
        ]
    }
    assert client.post("/api/v1/paper/world-state/bars", json=payload).status_code == 401
    headers = {"X-Operator-Token": "fixture-token"}
    assert (
        client.post("/api/v1/paper/world-state/bars", json=payload, headers=headers).json()[
            "inserted_count"
        ]
        == 4
    )
    assert (
        client.post("/api/v1/paper/world-state/bars", json=payload, headers=headers).json()[
            "inserted_count"
        ]
        == 0
    )
    market = quote()
    assert (
        client.post(
            "/api/v1/paper/quotes",
            headers=headers,
            json={
                "instrument_id": market.instrument_id,
                "bid": str(market.bid),
                "ask": str(market.ask),
                "observed_at": market.observed_at.isoformat(),
                "world_state_version": market.world_state_version,
            },
        ).status_code
        == 200
    )
    result = client.get("/api/v1/world-state").json()
    assert result["instruments"][0]["regime"] == "TRENDING"
    assert client.get("/api/v1/orders").json()["orders"] == []


def test_snapshot_expiry_cannot_extend_the_life_of_source_evidence():
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    engine.record_bars(bars(), now=NOW)
    snapshot = engine.snapshot((quote(age=4),), now=NOW)
    assert snapshot.valid_until == NOW + timedelta(seconds=1)


def test_known_regime_policy_and_snapshot_uses_no_future_history():
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    engine.record_bars(bars(closes=("1", "1", "1", "1")), now=NOW)
    assert engine.snapshot((quote(),), now=NOW).instruments[0].regime == "RANGING"
    previous = engine.snapshot((quote(),), now=NOW)
    later = NOW + timedelta(seconds=1)
    engine.record_bars(
        (Bar("EURUSD", later, Decimal("2"), Decimal("2"), Decimal("2"), Decimal("2"), Decimal(1)),),
        now=later,
    )
    assert engine.snapshot((quote(),), now=NOW) == previous
    assert engine.snapshot((quote(),), now=later).instruments[0].regime == "HIGH_VOLATILITY"

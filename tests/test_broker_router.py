from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ai_quant_os.broker import BrokerRouter, BrokerUnavailable, SimulatedBroker
from ai_quant_os.contracts import OrderCommand


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def command(*, route: str = "PAPER", instrument: str = "EURUSD") -> OrderCommand:
    return OrderCommand(
        order_id="order-1",
        idempotency_key="one",
        risk_id="risk-1",
        broker_route=route,
        instrument_id=instrument,
        order_type="MARKET",
        side="BUY",
        quantity=Decimal("100"),
        limit_price=None,
        stop_price=None,
        time_in_force="IOC",
        expires_at=NOW + timedelta(minutes=1),
    )


def test_disabled_real_venue_cannot_receive_order() -> None:
    exness = SimulatedBroker("EXNESS", {"EURUSD": "EURUSDm"}, enabled=False)
    router = BrokerRouter([exness])
    with pytest.raises(BrokerUnavailable, match="disabled"):
        router.route(command(route="EXNESS"), now=NOW)
    assert exness.orders == ()


def test_unsupported_instrument_cannot_receive_order() -> None:
    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    with pytest.raises(BrokerUnavailable, match="unsupported"):
        BrokerRouter([paper]).route(command(instrument="GBPUSD"), now=NOW)
    assert paper.orders == ()


def test_disconnected_venue_cannot_receive_order() -> None:
    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    paper.disconnect()
    with pytest.raises(BrokerUnavailable, match="disconnected"):
        BrokerRouter([paper]).route(command(), now=NOW)


def test_reconnect_restores_paper_route_with_symbol_mapping() -> None:
    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD.sim"}, enabled=True)
    paper.disconnect()
    paper.reconnect()
    ack = BrokerRouter([paper]).route(command(), now=NOW)
    assert ack.venue_symbol == "EURUSD.sim"
    assert ack.broker_id == "PAPER"
    assert len(paper.orders) == 1


def test_simulated_broker_retry_is_idempotent() -> None:
    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    router = BrokerRouter([paper])
    first = router.route(command(), now=NOW)
    second = router.route(command(), now=NOW)
    assert first == second
    assert len(paper.orders) == 1

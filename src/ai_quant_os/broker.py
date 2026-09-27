from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Protocol

from ai_quant_os.contracts import OrderCommand, _require_aware


class BrokerUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class BrokerAck:
    broker_id: str
    broker_order_id: str
    venue_symbol: str
    status: str


class BrokerAdapter(Protocol):
    broker_id: str
    enabled: bool
    connected: bool
    symbol_map: dict[str, str]

    def place(self, command: OrderCommand) -> BrokerAck: ...


class SimulatedBroker:
    def __init__(self, broker_id: str, symbol_map: dict[str, str], *, enabled: bool) -> None:
        self.broker_id = broker_id
        self.symbol_map = dict(symbol_map)
        self.enabled = enabled
        self.connected = True
        self._orders: dict[str, tuple[OrderCommand, BrokerAck]] = {}
        self._lock = Lock()

    @property
    def orders(self) -> tuple[BrokerAck, ...]:
        with self._lock:
            return tuple(ack for _, ack in self._orders.values())

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True

    def place(self, command: OrderCommand) -> BrokerAck:
        with self._lock:
            existing = self._orders.get(command.idempotency_key)
            if existing is not None:
                if existing[0] != command:
                    raise ValueError("Broker idempotency key reused")
                return existing[1]
            ack = BrokerAck(
                self.broker_id,
                f"{self.broker_id}:{command.order_id}",
                self.symbol_map[command.instrument_id],
                "ACCEPTED",
            )
            self._orders[command.idempotency_key] = (command, ack)
            return ack


class BrokerRouter:
    def __init__(self, adapters: list[BrokerAdapter]) -> None:
        self._adapters = {adapter.broker_id: adapter for adapter in adapters}

    def snapshot(self) -> list[dict[str, str | bool | list[str]]]:
        return [
            {
                "broker_id": adapter.broker_id,
                "connection_state": "CONNECTED" if adapter.connected else "DISCONNECTED",
                "enabled": adapter.enabled,
                "instruments": sorted(adapter.symbol_map),
            }
            for adapter in self._adapters.values()
        ]

    def route(self, command: OrderCommand, *, now: datetime) -> BrokerAck:
        _require_aware(now)
        adapter = self._adapters.get(command.broker_route)
        if adapter is None or not adapter.enabled:
            raise BrokerUnavailable("Broker route is disabled")
        if not adapter.connected:
            raise BrokerUnavailable("Broker is disconnected")
        if command.instrument_id not in adapter.symbol_map or command.order_type != "MARKET":
            raise BrokerUnavailable("Instrument or order type is unsupported")
        if now >= command.expires_at:
            raise BrokerUnavailable("Order command expired")
        return adapter.place(command)

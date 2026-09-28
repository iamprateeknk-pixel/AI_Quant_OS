import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Protocol

from ai_quant_os.contracts import OrderCommand, _require_aware


class BrokerUnavailable(ValueError):
    pass


class BrokerOutcomeUnknown(BrokerUnavailable):
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

    def lookup(self, command: OrderCommand) -> BrokerAck | None: ...


class SimulatedBroker:
    def __init__(
        self,
        broker_id: str,
        symbol_map: dict[str, str],
        *,
        enabled: bool,
        store_path: Path | None = None,
    ) -> None:
        self.broker_id = broker_id
        self.symbol_map = dict(symbol_map)
        self.enabled = enabled
        self.connected = True
        self._orders: dict[str, tuple[OrderCommand, BrokerAck]] = {}
        self._lock = Lock()
        self._connection = (
            sqlite3.connect(str(store_path), check_same_thread=False, timeout=30)
            if store_path is not None
            else None
        )
        if self._connection is not None:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS simulated_broker_orders (
                    broker_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    ack_json TEXT NOT NULL,
                    PRIMARY KEY (broker_id, idempotency_key)
                )"""
            )
            self._connection.commit()

    @staticmethod
    def _encode_command(command: OrderCommand) -> str:
        def encode(value: Decimal | datetime) -> str:
            if isinstance(value, Decimal):
                return str(value.normalize())
            if isinstance(value, datetime):
                return value.isoformat()
            raise TypeError(f"Unsupported broker command value: {type(value)}")

        return json.dumps(asdict(command), default=encode, sort_keys=True)

    def _stored(self, command: OrderCommand) -> BrokerAck | None:
        if self._connection is None:
            existing = self._orders.get(command.idempotency_key)
            if existing is None:
                return None
            if existing[0] != command:
                raise ValueError("Broker idempotency key reused")
            return existing[1]
        row = self._connection.execute(
            """SELECT command_json, ack_json FROM simulated_broker_orders
            WHERE broker_id = ? AND idempotency_key = ?""",
            (self.broker_id, command.idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row[0] != self._encode_command(command):
            raise ValueError("Broker idempotency key reused")
        return BrokerAck(**json.loads(row[1]))

    @property
    def orders(self) -> tuple[BrokerAck, ...]:
        with self._lock:
            if self._connection is not None:
                rows = self._connection.execute(
                    "SELECT ack_json FROM simulated_broker_orders WHERE broker_id = ? ORDER BY rowid",
                    (self.broker_id,),
                ).fetchall()
                return tuple(BrokerAck(**json.loads(row[0])) for row in rows)
            return tuple(ack for _, ack in self._orders.values())

    def lookup(self, command: OrderCommand) -> BrokerAck | None:
        with self._lock:
            return self._stored(command)

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True

    def place(self, command: OrderCommand) -> BrokerAck:
        with self._lock:
            if self._connection is not None:
                self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._stored(command)
                if existing is not None:
                    if self._connection is not None:
                        self._connection.commit()
                    return existing
                ack = BrokerAck(
                    self.broker_id,
                    f"{self.broker_id}:{command.order_id}",
                    self.symbol_map[command.instrument_id],
                    "ACCEPTED",
                )
                if self._connection is None:
                    self._orders[command.idempotency_key] = (command, ack)
                else:
                    self._connection.execute(
                        "INSERT INTO simulated_broker_orders VALUES (?, ?, ?, ?)",
                        (
                            self.broker_id,
                            command.idempotency_key,
                            self._encode_command(command),
                            json.dumps(asdict(ack), sort_keys=True),
                        ),
                    )
                    self._connection.commit()
                return ack
            except BaseException:
                if self._connection is not None:
                    self._connection.rollback()
                raise


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
        try:
            return adapter.place(command)
        except Exception as error:
            raise BrokerOutcomeUnknown(
                "Broker outcome is unknown; reconcile before new risk"
            ) from error

    def lookup(self, command: OrderCommand) -> BrokerAck | None:
        adapter = self._adapters.get(command.broker_route)
        if adapter is None:
            raise BrokerUnavailable("Broker route does not exist")
        return adapter.lookup(command)

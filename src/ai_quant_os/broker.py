import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Protocol

from ai_quant_os.broker_capabilities import BrokerEligibility, default_eligibility
from ai_quant_os.contracts import OrderCommand, _require_aware
from ai_quant_os.portfolio import PaperFill


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

    def fills(self, command: OrderCommand) -> tuple[PaperFill, ...]: ...

    def all_fills(self) -> tuple[PaperFill, ...]: ...


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
        self._fills: dict[str, PaperFill] = {}
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
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS simulated_broker_fills (
                    broker_id TEXT NOT NULL,
                    fill_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    fill_json TEXT NOT NULL,
                    PRIMARY KEY (broker_id, fill_id)
                )"""
            )
            self._connection.commit()

    @staticmethod
    def _encode_command(command: OrderCommand) -> str:
        return json.dumps(asdict(command), default=SimulatedBroker._encode_value, sort_keys=True)

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

    @staticmethod
    def _encode_fill(fill: PaperFill) -> str:
        return json.dumps(asdict(fill), default=SimulatedBroker._encode_value, sort_keys=True)

    @staticmethod
    def _encode_value(value: Decimal | datetime) -> str:
        if isinstance(value, Decimal):
            return str(value.normalize())
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Unsupported broker value: {type(value)}")

    @staticmethod
    def _decode_fill(raw: str) -> PaperFill:
        data = json.loads(raw)
        for field in ("quantity", "price", "fee"):
            data[field] = Decimal(data[field])
        data["observed_at"] = datetime.fromisoformat(data["observed_at"])
        return PaperFill(**data)

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

    def fills(self, command: OrderCommand) -> tuple[PaperFill, ...]:
        with self._lock:
            if self._stored(command) is None:
                raise ValueError("Broker order does not exist")
            if self._connection is None:
                return tuple(
                    fill for fill in self._fills.values() if fill.order_id == command.order_id
                )
            rows = self._connection.execute(
                """SELECT fill_json FROM simulated_broker_fills
                WHERE broker_id = ? AND order_id = ? ORDER BY rowid""",
                (self.broker_id, command.order_id),
            ).fetchall()
            return tuple(self._decode_fill(row[0]) for row in rows)

    def all_fills(self) -> tuple[PaperFill, ...]:
        with self._lock:
            if self._connection is None:
                return tuple(self._fills.values())
            rows = self._connection.execute(
                "SELECT fill_json FROM simulated_broker_fills WHERE broker_id = ? ORDER BY rowid",
                (self.broker_id,),
            ).fetchall()
            return tuple(self._decode_fill(row[0]) for row in rows)

    def report_fill(self, command: OrderCommand, fill: PaperFill) -> PaperFill:
        if fill.source != "PAPER_BROKER":
            raise ValueError("Broker fill source is required")
        if (
            fill.order_id != command.order_id
            or fill.instrument_id != command.instrument_id
            or fill.side != command.side
        ):
            raise ValueError("Broker fill does not match order")
        with self._lock:
            if self._connection is not None:
                self._connection.execute("BEGIN IMMEDIATE")
            try:
                if self._stored(command) is None:
                    raise ValueError("Broker order does not exist")
                encoded = self._encode_fill(fill)
                if self._connection is None:
                    prior = self._fills.get(fill.fill_id)
                    if prior is not None:
                        if prior != fill:
                            raise ValueError("Broker fill id reused with different details")
                        return prior
                    existing = tuple(
                        item for item in self._fills.values() if item.order_id == command.order_id
                    )
                else:
                    row = self._connection.execute(
                        """SELECT fill_json FROM simulated_broker_fills
                        WHERE broker_id = ? AND fill_id = ?""",
                        (self.broker_id, fill.fill_id),
                    ).fetchone()
                    if row is not None:
                        if row[0] != encoded:
                            raise ValueError("Broker fill id reused with different details")
                        self._connection.commit()
                        return self._decode_fill(row[0])
                    rows = self._connection.execute(
                        """SELECT fill_json FROM simulated_broker_fills
                        WHERE broker_id = ? AND order_id = ?""",
                        (self.broker_id, command.order_id),
                    ).fetchall()
                    existing = tuple(self._decode_fill(row[0]) for row in rows)
                if (
                    sum((item.quantity for item in existing), Decimal(0)) + fill.quantity
                    > command.quantity
                ):
                    raise ValueError("Broker fills exceed order quantity")
                if self._connection is None:
                    self._fills[fill.fill_id] = fill
                else:
                    self._connection.execute(
                        "INSERT INTO simulated_broker_fills VALUES (?, ?, ?, ?)",
                        (self.broker_id, fill.fill_id, fill.order_id, encoded),
                    )
                    self._connection.commit()
                return fill
            except BaseException:
                if self._connection is not None:
                    self._connection.rollback()
                raise

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
                if self.enabled is not True or self.connected is not True:
                    raise BrokerUnavailable("Paper simulator is disabled or disconnected")
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
    def __init__(
        self,
        adapters: list[BrokerAdapter],
        *,
        eligibility: dict[str, BrokerEligibility] | None = None,
    ) -> None:
        if len({adapter.broker_id for adapter in adapters}) != len(adapters):
            raise ValueError("Duplicate broker identity")
        self._adapters = {adapter.broker_id: adapter for adapter in adapters}
        if eligibility is not None and (
            set(eligibility) - set(self._adapters)
            or any(not isinstance(value, BrokerEligibility) for value in eligibility.values())
        ):
            raise ValueError("Invalid broker eligibility binding")
        self._eligibility = {
            identity: default_eligibility(identity)
            if eligibility is None
            else eligibility.get(identity, BrokerEligibility())
            for identity in self._adapters
        }

    def eligibility(self, broker_id: str) -> BrokerEligibility:
        return self._eligibility.get(broker_id, BrokerEligibility())

    def snapshot(self) -> list[dict[str, str | bool | list[str]]]:
        return [
            {
                "broker_id": adapter.broker_id,
                "connection_state": "CONNECTED" if adapter.connected is True else "DISCONNECTED",
                "enabled": adapter.enabled is True,
                "instruments": sorted(adapter.symbol_map),
            }
            for adapter in self._adapters.values()
        ]

    def route(self, command: OrderCommand, *, now: datetime) -> BrokerAck:
        _require_aware(now)
        adapter = self._adapters.get(command.broker_route)
        if adapter is None or adapter.enabled is not True:
            raise BrokerUnavailable("Broker route is disabled")
        if command.broker_route != "PAPER":
            raise BrokerUnavailable("External broker execution is not released")
        if not self.eligibility(command.broker_route).allows_paper_orders():
            raise BrokerUnavailable("Broker eligibility is not confirmed for local simulation")
        if adapter.connected is not True:
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

    def fills(self, command: OrderCommand) -> tuple[PaperFill, ...]:
        adapter = self._adapters.get(command.broker_route)
        if adapter is None:
            raise BrokerUnavailable("Broker route does not exist")
        return adapter.fills(command)

    def all_fills(self, broker_route: str) -> tuple[PaperFill, ...]:
        adapter = self._adapters.get(broker_route)
        if adapter is None:
            raise BrokerUnavailable("Broker route does not exist")
        return adapter.all_fills()

    def simulate_fill(self, command: OrderCommand, fill: PaperFill) -> PaperFill:
        adapter = self._adapters.get(command.broker_route)
        if not isinstance(adapter, SimulatedBroker):
            raise BrokerUnavailable("Paper simulator is unavailable")
        return adapter.report_fill(command, fill)

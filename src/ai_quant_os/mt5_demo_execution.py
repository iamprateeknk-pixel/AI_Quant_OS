"""Explicitly admitted, idempotent Exness MT5 demo order submission.

This module is separate from the PAPER router and never accepts a live account.
The caller must run native SDK operations in the supervised MT5 worker.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from ai_quant_os.contracts import OrderCommand
from ai_quant_os.mt5_account_sync import account_scope
from ai_quant_os.mt5_demo_observation import (
    DemoCaptureRequest,
    ObservationModel,
    Withheld,
    account_matches,
    utc,
)


class DemoOrderEnrollment(ObservationModel):
    connection: DemoCaptureRequest
    enabled: bool = Field(default=False, strict=True)
    submission_ack: Literal["SUBMIT_EXNESS_DEMO_ONLY"] | None = None
    max_order_lots: Decimal

    @model_validator(mode="after")
    def explicit_demo_gate(self):
        if (
            not self.max_order_lots.is_finite()
            or self.max_order_lots <= 0
            or self.max_order_lots > Decimal("10")
        ):
            raise ValueError("Demo order lot cap is outside the supported bound")
        if self.enabled:
            if not self.connection.enabled or self.submission_ack != "SUBMIT_EXNESS_DEMO_ONLY":
                raise ValueError("Explicit enabled demo order enrollment is required")
        elif self.submission_ack is not None:
            raise ValueError("Disabled demo order enrollment cannot carry submission authority")
        return self


class DemoOrderReceipt(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    broker_id: Literal["EXNESS_MT5_DEMO"] = "EXNESS_MT5_DEMO"
    state: Literal["DISABLED", "WITHHELD", "REJECTED", "ACKNOWLEDGED", "UNKNOWN"]
    code: (
        Literal[
            "DISABLED",
            "INVALID_COMMAND",
            "CONNECTION",
            "ACCOUNT",
            "QUOTE",
            "VOLUME",
            "ORDER_CHECK",
            "REMOTE_REJECTED",
            "NATIVE_FAILURE",
            "CLEANUP_FAILURE",
            "IDEMPOTENCY_CONFLICT",
        ]
        | None
    ) = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    order_id: str = Field(min_length=1, max_length=128)
    instrument_id: Literal["EURUSD", "GBPUSD", "USDJPY", "EURJPY"]
    side: Literal["BUY", "SELL"]
    quantity_units: Decimal
    account_scope_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    broker_order_id: str | None = Field(default=None, max_length=64)
    broker_deal_id: str | None = Field(default=None, max_length=64)
    filled_lots: Decimal | None = None
    average_price: Decimal | None = None
    trace_id: str = Field(default_factory=lambda: str(uuid4()), pattern=r"^[0-9a-f-]{36}$")
    demo_only: Literal[True] = True

    @model_validator(mode="after")
    def positive_execution_values(self):
        if not self.quantity_units.is_finite() or self.quantity_units <= 0:
            raise ValueError("Demo order quantity must be positive and finite")
        if self.filled_lots is not None and (
            not self.filled_lots.is_finite() or self.filled_lots < 0
        ):
            raise ValueError("Demo fill volume must be finite and nonnegative")
        if self.average_price is not None and (
            not self.average_price.is_finite() or self.average_price <= 0
        ):
            raise ValueError("Demo fill price must be positive and finite")
        return self


def _command_json(command: OrderCommand) -> str:
    return json.dumps(
        {
            "order_id": command.order_id,
            "idempotency_key": command.idempotency_key,
            "risk_id": command.risk_id,
            "broker_route": command.broker_route,
            "instrument_id": command.instrument_id,
            "order_type": command.order_type,
            "side": command.side,
            "quantity": str(command.quantity.normalize()),
            "limit_price": None if command.limit_price is None else str(command.limit_price),
            "stop_price": None if command.stop_price is None else str(command.stop_price),
            "time_in_force": command.time_in_force,
            "expires_at": command.expires_at.astimezone(timezone.utc).isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class DemoOrderStore:
    """Durable one-shot reservations; uncertain submissions are never retried."""

    def __init__(self, path: Path):
        self._connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS exness_demo_order_attempts (
                idempotency_key TEXT PRIMARY KEY,
                command_json TEXT NOT NULL,
                state TEXT NOT NULL,
                receipt_json TEXT,
                account_scope_ref TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )

    def close(self):
        self._connection.close()

    def reserve(self, command: OrderCommand, now: datetime, scope: str) -> DemoOrderReceipt | None:
        encoded = _command_json(command)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT command_json, state, receipt_json, account_scope_ref FROM exness_demo_order_attempts WHERE idempotency_key=?",
                (command.idempotency_key,),
            ).fetchone()
            if row is not None:
                if row[0] != encoded or row[3] != scope:
                    raise ValueError("idempotency key reused with changed order")
                if row[2] is not None:
                    receipt = DemoOrderReceipt.model_validate_json(row[2])
                else:
                    receipt = _receipt(command, "UNKNOWN", "NATIVE_FAILURE", row[3])
                self._connection.commit()
                return receipt
            self._connection.execute(
                "INSERT INTO exness_demo_order_attempts VALUES (?, ?, ?, ?, ?, ?)",
                (command.idempotency_key, encoded, "RESERVED", None, scope, now.isoformat()),
            )
            self._connection.commit()
            return None
        except BaseException:
            self._connection.rollback()
            raise

    def finish(self, command: OrderCommand, receipt: DemoOrderReceipt):
        self._connection.execute(
            "UPDATE exness_demo_order_attempts SET state=?, receipt_json=? WHERE idempotency_key=?",
            (receipt.state, receipt.model_dump_json(), command.idempotency_key),
        )

    def attempt(self, key: str):
        return self._connection.execute(
            "SELECT command_json, state, receipt_json, account_scope_ref, created_at "
            "FROM exness_demo_order_attempts WHERE idempotency_key=?",
            (key,),
        ).fetchone()


def _receipt(command, state, code, scope, **kwargs):
    return DemoOrderReceipt(
        state=state,
        code=code,
        idempotency_key=command.idempotency_key,
        order_id=command.order_id,
        instrument_id=command.instrument_id,
        side=command.side,
        quantity_units=command.quantity,
        account_scope_ref=scope,
        **kwargs,
    )


def _valid_command(command: OrderCommand, enrollment: DemoOrderEnrollment, now: datetime):
    if (
        not isinstance(command, OrderCommand)
        or command.broker_route != "EXNESS_MT5_DEMO"
        or command.order_type != "MARKET"
        or command.instrument_id
        not in {item.instrument_id for item in enrollment.connection.symbols}
        or command.side not in {"BUY", "SELL"}
        or command.limit_price is not None
        or command.stop_price is not None
        or command.time_in_force not in {"IOC", "FOK"}
        or not command.order_id
        or not command.idempotency_key
        or len(command.order_id) > 128
        or len(command.idempotency_key) > 128
        or not command.quantity.is_finite()
        or command.quantity <= 0
        or command.expires_at.tzinfo is None
        or command.expires_at.utcoffset() != timedelta(0)
        or command.expires_at <= now
    ):
        raise Withheld("INVALID_COMMAND")


def _lots(quantity_units: Decimal, contract_size: Decimal, info, max_order_lots: Decimal):
    try:
        lots = quantity_units / contract_size
        minimum = Decimal(str(info.volume_min))
        maximum = min(Decimal(str(info.volume_max)), max_order_lots)
        step = Decimal(str(info.volume_step))
        if (
            not all(value.is_finite() and value > 0 for value in (lots, minimum, maximum, step))
            or lots < minimum
            or lots > maximum
            or (lots - minimum) % step != 0
        ):
            raise Withheld("VOLUME")
        return lots
    except (InvalidOperation, TypeError, ValueError, ArithmeticError):
        raise Withheld("VOLUME") from None


def submit_demo_order(
    enrollment: DemoOrderEnrollment,
    command: OrderCommand,
    native,
    store_path: Path,
    *,
    clock=lambda: datetime.now(timezone.utc),
) -> DemoOrderReceipt:
    """Submit one market order after explicit demo admission; never called by PAPER routing."""
    if len(command.order_id) > 128 or len(command.idempotency_key) > 128:
        raise ValueError("Demo order and idempotency identifiers must be at most 128 characters")
    scope = account_scope(enrollment.connection)
    if not enrollment.enabled:
        return _receipt(command, "DISABLED", "DISABLED", scope)
    now = utc(clock())
    try:
        _valid_command(command, enrollment, now)
    except Withheld as exc:
        return _receipt(command, "WITHHELD", exc.code, scope)

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = DemoOrderStore(store_path)
    try:
        prior = store.reserve(command, now, scope)
        if prior is not None:
            return prior
        result = _submit_reserved(enrollment, command, native, clock, scope)
        store.finish(command, result)
        return result
    finally:
        store.close()


def reconcile_demo_order(
    enrollment: DemoOrderEnrollment,
    command: OrderCommand,
    native,
    store_path: Path,
    *,
    clock=lambda: datetime.now(timezone.utc),
) -> DemoOrderReceipt:
    """Resolve an UNKNOWN reservation from one exact, read-only broker history match."""
    scope = account_scope(enrollment.connection)
    if not enrollment.enabled:
        return _receipt(command, "DISABLED", "DISABLED", scope)
    store = DemoOrderStore(store_path)
    try:
        row = store.attempt(command.idempotency_key)
        if row is None or row[0] != _command_json(command) or row[3] != scope:
            return _receipt(command, "WITHHELD", "IDEMPOTENCY_CONFLICT", scope)
        if row[2] is not None:
            previous = DemoOrderReceipt.model_validate_json(row[2])
            if previous.state != "UNKNOWN":
                return previous
        started = datetime.fromisoformat(row[4]).astimezone(timezone.utc)
        now = utc(clock())
        if now < started or now - started > timedelta(days=7):
            return _receipt(command, "UNKNOWN", "NATIVE_FAILURE", scope)
        result = _reconcile_reserved(enrollment, command, native, started, now, scope)
        if result.state == "ACKNOWLEDGED":
            store.finish(command, result)
        return result
    finally:
        store.close()


def _reconcile_reserved(enrollment, command, native, started, now, scope):
    connected = False
    try:
        if getattr(native, "__version__", None) != enrollment.connection.sdk_version:
            raise Withheld("CONNECTION")
        if (
            native.initialize(
                enrollment.connection.terminal_path,
                login=enrollment.connection.expected_login,
                password=enrollment.connection.password.get_secret_value(),
                server=enrollment.connection.expected_server.get_secret_value(),
                timeout=3000,
                portable=False,
            )
            is not True
        ):
            raise Withheld("CONNECTION")
        connected = True
        account_matches(enrollment.connection, native)
        symbol = next(
            item
            for item in enrollment.connection.symbols
            if item.instrument_id == command.instrument_id
        )
        info = native.symbol_info(symbol.venue_symbol)
        if info is None or getattr(info, "name", None) != symbol.venue_symbol:
            raise Withheld("QUOTE")
        contract_size = Decimal(str(info.trade_contract_size))
        lots = command.quantity / contract_size
        digest = sha256(command.idempotency_key.encode()).hexdigest()
        magic = int(digest[:8], 16) & 0x7FFFFFFF
        comment = f"AQOS-{digest[:16]}"
        start = started - timedelta(minutes=2)
        rows = native.history_orders_get(start, now)
        if rows is None or len(rows) > 5000:
            raise Withheld("NATIVE_FAILURE")
        expected_type = native.ORDER_TYPE_BUY if command.side == "BUY" else native.ORDER_TYPE_SELL
        matches = [
            item
            for item in rows
            if type(getattr(item, "ticket", None)) is int
            and item.ticket > 0
            and type(getattr(item, "magic", None)) is int
            and getattr(item, "magic", None) == magic
            and getattr(item, "comment", None) == comment
            and getattr(item, "symbol", None) == symbol.venue_symbol
            and type(getattr(item, "type", None)) is int
            and getattr(item, "type", None) == expected_type
            and Decimal(str(getattr(item, "volume_initial", "NaN"))) == lots
            and type(getattr(item, "setup_time_msc", None)) is int
            and int(start.timestamp() * 1000) <= item.setup_time_msc <= int(now.timestamp() * 1000)
        ]
        if len(matches) != 1:
            raise Withheld("NATIVE_FAILURE")
        order = matches[0]
        accepted_states = {
            getattr(native, "ORDER_STATE_PLACED", 1),
            getattr(native, "ORDER_STATE_PARTIAL", 3),
            getattr(native, "ORDER_STATE_FILLED", 4),
        }
        order_state = getattr(order, "state", None)
        if type(order_state) is not int or order_state not in accepted_states:
            raise Withheld("NATIVE_FAILURE")
        deals = native.history_deals_get(ticket=order.ticket)
        if deals is None or len(deals) > 5000:
            raise Withheld("NATIVE_FAILURE")
        order_deals = [item for item in deals if getattr(item, "order", None) == order.ticket]
        filled = Decimal("0")
        notional = Decimal("0")
        deal_ids = []
        for deal in order_deals:
            if (
                type(getattr(deal, "magic", None)) is not int
                or type(getattr(deal, "type", None)) is not int
                or getattr(deal, "magic", None) != magic
                or getattr(deal, "comment", None) != comment
                or getattr(deal, "symbol", None) != symbol.venue_symbol
                or getattr(deal, "type", None) != expected_type
            ):
                raise Withheld("NATIVE_FAILURE")
            volume = Decimal(str(getattr(deal, "volume", "NaN")))
            price = Decimal(str(getattr(deal, "price", "NaN")))
            if not volume.is_finite() or volume <= 0 or not price.is_finite() or price <= 0:
                raise Withheld("NATIVE_FAILURE")
            filled += volume
            notional += volume * price
            deal_ids.append(getattr(deal, "ticket", None))
        if filled > lots or (
            deal_ids
            and (
                len(deal_ids) != len(order_deals)
                or any(type(i) is not int or i <= 0 for i in deal_ids)
                or len(set(deal_ids)) != len(deal_ids)
            )
        ):
            raise Withheld("NATIVE_FAILURE")
        return _receipt(
            command,
            "ACKNOWLEDGED",
            None,
            scope,
            broker_order_id=str(order.ticket),
            broker_deal_id=str(deal_ids[0]) if len(deal_ids) == 1 else None,
            filled_lots=filled if filled else None,
            average_price=notional / filled if filled else None,
        )
    except Withheld as exc:
        return _receipt(command, "UNKNOWN", exc.code, scope)
    except Exception:
        return _receipt(command, "UNKNOWN", "NATIVE_FAILURE", scope)
    finally:
        if connected:
            try:
                native.shutdown()
            except Exception:
                pass


def _submit_reserved(enrollment, command, native, clock, scope):
    result = _receipt(command, "UNKNOWN", "NATIVE_FAILURE", scope)
    connected = False
    try:
        if getattr(native, "__version__", None) != enrollment.connection.sdk_version:
            raise Withheld("CONNECTION")
        now = utc(clock())
        _valid_command(command, enrollment, now)
        if (
            native.initialize(
                enrollment.connection.terminal_path,
                login=enrollment.connection.expected_login,
                password=enrollment.connection.password.get_secret_value(),
                server=enrollment.connection.expected_server.get_secret_value(),
                timeout=3000,
                portable=False,
            )
            is not True
        ):
            raise Withheld("CONNECTION")
        connected = True
        account_matches(enrollment.connection, native)
        symbol = next(
            item
            for item in enrollment.connection.symbols
            if item.instrument_id == command.instrument_id
        )
        info = native.symbol_info(symbol.venue_symbol)
        tick = native.symbol_info_tick(symbol.venue_symbol)
        if (
            info is None
            or tick is None
            or getattr(info, "name", None) != symbol.venue_symbol
            or getattr(info, "currency_base", None) != command.instrument_id[:3]
            or getattr(info, "currency_profit", None) != command.instrument_id[3:]
            or type(getattr(tick, "time_msc", None)) is not int
            or tick.time_msc <= 0
        ):
            raise Withheld("QUOTE")
        observed_at = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            milliseconds=tick.time_msc
        )
        if observed_at > now or now - observed_at >= timedelta(seconds=3):
            raise Withheld("QUOTE")
        contract_size = Decimal(str(info.trade_contract_size))
        if not contract_size.is_finite() or contract_size <= 0:
            raise Withheld("VOLUME")
        lots = _lots(command.quantity, contract_size, info, enrollment.max_order_lots)
        price = Decimal(str(tick.ask if command.side == "BUY" else tick.bid))
        if not price.is_finite() or price <= 0:
            raise Withheld("QUOTE")
        key_digest = sha256(command.idempotency_key.encode()).hexdigest()
        order_request = {
            "action": native.TRADE_ACTION_DEAL,
            "symbol": symbol.venue_symbol,
            "volume": float(lots),
            "type": native.ORDER_TYPE_BUY if command.side == "BUY" else native.ORDER_TYPE_SELL,
            "price": float(price),
            "deviation": 10,
            "magic": int(key_digest[:8], 16) & 0x7FFFFFFF,
            "comment": f"AQOS-{key_digest[:16]}",
            "type_time": native.ORDER_TIME_GTC,
            "type_filling": (
                native.ORDER_FILLING_IOC
                if command.time_in_force == "IOC"
                else native.ORDER_FILLING_FOK
            ),
        }
        check = native.order_check(order_request)
        if check is None or getattr(check, "retcode", None) != 0:
            raise Withheld("ORDER_CHECK")
        _valid_command(command, enrollment, utc(clock()))
        account_matches(enrollment.connection, native)
        latest_time = utc(clock())
        latest_tick = native.symbol_info_tick(symbol.venue_symbol)
        if (
            latest_tick is None
            or type(getattr(latest_tick, "time_msc", None)) is not int
            or latest_tick.time_msc <= 0
        ):
            raise Withheld("QUOTE")
        latest_at = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            milliseconds=latest_tick.time_msc
        )
        latest_price = Decimal(str(latest_tick.ask if command.side == "BUY" else latest_tick.bid))
        if (
            latest_at > latest_time
            or latest_time - latest_at >= timedelta(seconds=3)
            or not latest_price.is_finite()
            or latest_price <= 0
        ):
            raise Withheld("QUOTE")
        order_request["price"] = float(latest_price)
        _valid_command(command, enrollment, utc(clock()))
        try:
            response = native.order_send(order_request)
        except Exception:
            return _receipt(command, "UNKNOWN", "NATIVE_FAILURE", scope)
        if response is None:
            return _receipt(command, "UNKNOWN", "NATIVE_FAILURE", scope)
        retcode = getattr(response, "retcode", None)
        if type(retcode) is not int:
            return _receipt(command, "UNKNOWN", "NATIVE_FAILURE", scope)
        if retcode not in {
            native.TRADE_RETCODE_DONE,
            native.TRADE_RETCODE_PLACED,
            getattr(native, "TRADE_RETCODE_DONE_PARTIAL", -1),
        }:
            return _receipt(command, "REJECTED", "REMOTE_REJECTED", scope)
        broker_order_id = getattr(response, "order", None)
        if type(broker_order_id) is not int or broker_order_id <= 0:
            return _receipt(command, "UNKNOWN", "NATIVE_FAILURE", scope)
        filled_lots = Decimal(str(getattr(response, "volume", 0)))
        average_price = Decimal(str(getattr(response, "price", 0)))
        result = _receipt(
            command,
            "ACKNOWLEDGED",
            None,
            scope,
            broker_order_id=str(broker_order_id),
            broker_deal_id=str(getattr(response, "deal", "")) or None,
            filled_lots=filled_lots if filled_lots > 0 else None,
            average_price=average_price if average_price > 0 else None,
        )
    except Withheld as exc:
        result = _receipt(command, "WITHHELD", exc.code, scope)
    except Exception:
        result = _receipt(command, "UNKNOWN", "NATIVE_FAILURE", scope)
    finally:
        if connected:
            try:
                native.shutdown()
            except Exception:
                if result.state == "ACKNOWLEDGED":
                    result = _receipt(command, "UNKNOWN", "CLEANUP_FAILURE", scope)
    return result

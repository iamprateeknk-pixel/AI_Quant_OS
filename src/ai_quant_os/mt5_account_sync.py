"""Normalize sequential demo account reads; no portfolio or order authority."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import Field, UUID4, model_validator

from ai_quant_os.mt5_demo_observation import (
    DemoCaptureRequest,
    DemoQuote,
    DemoSdk,
    ObservationModel,
    Withheld,
    account_matches,
    open_demo,
    read_quote,
    utc,
)

ORDER_KINDS = (
    "BUY",
    "SELL",
    "BUY_LIMIT",
    "SELL_LIMIT",
    "BUY_STOP",
    "SELL_STOP",
    "BUY_STOP_LIMIT",
    "SELL_STOP_LIMIT",
)
ORDER_STATES = {
    0: "STARTED",
    1: "PLACED",
    3: "PARTIAL",
    7: "REQUEST_ADD",
    8: "REQUEST_MODIFY",
    9: "REQUEST_CANCEL",
}
DEAL_KINDS = (
    "BUY",
    "SELL",
    "BALANCE",
    "CREDIT",
    "CHARGE",
    "CORRECTION",
    "BONUS",
    "COMMISSION",
    "COMMISSION_DAILY",
    "COMMISSION_MONTHLY",
    "COMMISSION_AGENT_DAILY",
    "COMMISSION_AGENT_MONTHLY",
    "INTEREST",
    "BUY_CANCELED",
    "SELL_CANCELED",
    "DIVIDEND",
    "DIVIDEND_FRANKED",
    "TAX",
)
ENTRY_KINDS = ("IN", "OUT", "INOUT", "OUT_BY")
MARGIN_MODES = ("NETTING", "EXCHANGE", "HEDGING")
Positive = Field(gt=0, allow_inf_nan=False)
Finite = Field(allow_inf_nan=False)


class DemoSyncRequest(ObservationModel):
    connection: DemoCaptureRequest
    history_start: datetime
    history_end: datetime

    @model_validator(mode="after")
    def bounded_window(self):
        if (
            self.history_start.tzinfo is None
            or self.history_end.tzinfo is None
            or self.history_start.utcoffset() != timedelta(0)
            or self.history_end.utcoffset() != timedelta(0)
            or not timedelta(0) < self.history_end - self.history_start <= timedelta(days=1)
        ):
            raise ValueError("History must be an explicit UTC window of at most one day")
        return self

    @property
    def enabled(self):
        return self.connection.enabled


class AccountView(ObservationModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    balance: Decimal = Finite
    credit: Decimal = Field(ge=0, allow_inf_nan=False)
    equity: Decimal = Finite
    profit: Decimal = Finite
    margin: Decimal = Field(ge=0, allow_inf_nan=False)
    margin_free: Decimal = Finite
    margin_mode: Literal["NETTING", "EXCHANGE", "HEDGING"]
    leverage: int = Field(gt=0, strict=True)


class PositionView(ObservationModel):
    ticket: int = Field(gt=0, strict=True)
    identifier: int = Field(gt=0, strict=True)
    instrument_id: str
    venue_symbol: str
    side: Literal["BUY", "SELL"]
    volume_lots: Decimal = Positive
    quantity_units: Decimal = Positive
    quantity_basis: Literal["CURRENT_SYMBOL_METADATA"] = "CURRENT_SYMBOL_METADATA"
    price_open: Decimal = Positive
    price_current: Decimal = Positive
    profit: Decimal = Finite
    swap: Decimal = Finite
    currency: str
    created_at: datetime
    updated_at: datetime


class OrderView(ObservationModel):
    ticket: int = Field(gt=0, strict=True)
    instrument_id: str
    venue_symbol: str
    kind: str
    state: str
    initial_lots: Decimal = Positive
    remaining_lots: Decimal = Field(ge=0, allow_inf_nan=False)
    price_open: Decimal = Field(ge=0, allow_inf_nan=False)
    stop_limit_price: Decimal = Field(ge=0, allow_inf_nan=False)
    created_at: datetime


class DealMoney(ObservationModel):
    ticket: int = Field(gt=0, strict=True)
    event_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    order_ticket: int = Field(ge=0, strict=True)
    position_identifier: int = Field(ge=0, strict=True)
    observed_at: datetime
    currency: str
    profit: Decimal = Finite
    commission: Decimal = Finite
    swap: Decimal = Finite
    fee: Decimal = Finite


class FillView(DealMoney):
    instrument_id: str
    venue_symbol: str
    side: Literal["BUY", "SELL"]
    entry: Literal["IN", "OUT", "INOUT", "OUT_BY"]
    volume_lots: Decimal = Positive
    price: Decimal = Positive
    quantity_units: Decimal = Positive
    quantity_basis: Literal["CURRENT_SYMBOL_METADATA_UNVERIFIED_AT_EXECUTION"] = (
        "CURRENT_SYMBOL_METADATA_UNVERIFIED_AT_EXECUTION"
    )


class CashEvent(DealMoney):
    kind: str


class SyncBatch(ObservationModel):
    enrollment_id: UUID4
    sdk_version: str
    account_scope_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    capture_started_at: datetime
    observed_at: datetime
    valid_until: datetime
    history_start: datetime
    history_end: datetime
    account: AccountView
    quotes: tuple[DemoQuote, ...]
    positions: tuple[PositionView, ...] = Field(max_length=8)
    orders: tuple[OrderView, ...] = Field(max_length=8)
    fills: tuple[FillView, ...] = Field(max_length=16)
    cash_events: tuple[CashEvent, ...] = Field(max_length=16)
    consistency: Literal["NON_ATOMIC_SEQUENTIAL_READ"] = "NON_ATOMIC_SEQUENTIAL_READ"
    account_freshness: Literal["UNVERIFIED"] = "UNVERIFIED"
    history_coverage: Literal["DECLARED_WINDOW_ONLY"] = "DECLARED_WINDOW_ONLY"


class DemoSyncReport(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_kind: Literal["ACCOUNT_SYNC"] = "ACCOUNT_SYNC"
    broker_id: Literal["EXNESS_MT5_DEMO"] = "EXNESS_MT5_DEMO"
    state: Literal["DISABLED", "WITHHELD", "CAPTURED"]
    code: (
        Literal[
            "DISABLED",
            "SDK_VERSION",
            "CONNECTION",
            "ACCOUNT",
            "QUOTE",
            "CLOCK",
            "NATIVE_FAILURE",
            "CLEANUP_FAILURE",
            "HISTORY_WINDOW",
            "DATA_UNAVAILABLE",
            "DATA_INVALID",
            "UNMAPPED_SYMBOL",
            "CAPACITY",
            "RESULT_LIMIT",
        ]
        | None
    )
    batch: SyncBatch | None = None
    trace_id: UUID4 = Field(default_factory=uuid4)
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"


class SyncSdk(DemoSdk, Protocol):
    def positions_get(self) -> object: ...
    def orders_get(self) -> object: ...
    def history_deals_get(self, start: datetime, end: datetime) -> object: ...


class SyncWithheld(Exception):
    def __init__(self, code):
        self.code = code


def account_scope(request: DemoCaptureRequest) -> str:
    values = [
        str(request.enrollment_id),
        request.expected_login,
        request.expected_server.get_secret_value(),
        request.expected_company,
        request.expected_currency,
    ]
    return sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def event_ref(scope: str, ticket: int) -> str:
    return sha256(json.dumps([scope, "DEAL", ticket], separators=(",", ":")).encode()).hexdigest()


def number(native, field) -> Decimal:
    value = Decimal(str(getattr(native, field)))
    if not value.is_finite():
        raise SyncWithheld("DATA_INVALID")
    return value


def integer(native, field) -> int:
    value = getattr(native, field)
    if type(value) is not int:
        raise SyncWithheld("DATA_INVALID")
    return value


def timestamp(native, field) -> datetime:
    value = integer(native, field)
    if value <= 0:
        raise SyncWithheld("DATA_INVALID")
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)


def rows(native_rows, limit):
    if native_rows is None:
        raise SyncWithheld("DATA_UNAVAILABLE")
    if not isinstance(native_rows, (tuple, list)):
        raise SyncWithheld("DATA_INVALID")
    if len(native_rows) > limit:
        raise SyncWithheld("CAPACITY")
    tickets = [integer(row, "ticket") for row in native_rows]
    if len(set(tickets)) != len(tickets):
        raise SyncWithheld("DATA_INVALID")
    return sorted(native_rows, key=lambda row: row.ticket)


def mapped(native, quotes):
    symbol = getattr(native, "symbol")
    if symbol not in quotes:
        raise SyncWithheld("UNMAPPED_SYMBOL")
    return quotes[symbol]


def enum(native, field, choices):
    value = integer(native, field)
    valid = value in choices if isinstance(choices, dict) else 0 <= value < len(choices)
    if not valid:
        raise SyncWithheld("DATA_INVALID")
    return choices[value]


def read_account(account) -> AccountView:
    return AccountView(
        currency=account.currency,
        leverage=integer(account, "leverage"),
        margin_mode=enum(account, "margin_mode", MARGIN_MODES),
        **{
            field: number(account, field)
            for field in ("balance", "credit", "equity", "profit", "margin", "margin_free")
        },
    )


def read_positions(native, quotes, currency):
    positions = []
    for row in rows(native.positions_get(), 8):
        quote = mapped(row, quotes)
        lots = number(row, "volume")
        positions.append(
            PositionView(
                ticket=row.ticket,
                identifier=integer(row, "identifier"),
                instrument_id=quote.instrument_id,
                venue_symbol=quote.venue_symbol,
                side=enum(row, "type", ("BUY", "SELL")),
                volume_lots=lots,
                quantity_units=lots * quote.contract_size,
                price_open=number(row, "price_open"),
                price_current=number(row, "price_current"),
                profit=number(row, "profit"),
                swap=number(row, "swap"),
                currency=currency,
                created_at=timestamp(row, "time_msc"),
                updated_at=timestamp(row, "time_update_msc"),
            )
        )
    if len({p.identifier for p in positions}) != len(positions):
        raise SyncWithheld("DATA_INVALID")
    return tuple(positions)


def read_orders(native, quotes):
    orders = []
    for row in rows(native.orders_get(), 8):
        quote = mapped(row, quotes)
        item = OrderView(
            ticket=row.ticket,
            instrument_id=quote.instrument_id,
            venue_symbol=quote.venue_symbol,
            kind=enum(row, "type", ORDER_KINDS),
            state=enum(row, "state", ORDER_STATES),
            initial_lots=number(row, "volume_initial"),
            remaining_lots=number(row, "volume_current"),
            price_open=number(row, "price_open"),
            stop_limit_price=number(row, "price_stoplimit"),
            created_at=timestamp(row, "time_setup_msc"),
        )
        if (
            item.remaining_lots > item.initial_lots
            or (row.type >= 2 and item.price_open <= 0)
            or (row.type >= 6 and item.stop_limit_price <= 0)
        ):
            raise SyncWithheld("DATA_INVALID")
        orders.append(item)
    return tuple(orders)


def read_deals(request, native, quotes, scope):
    fills, cash = [], []
    for row in rows(native.history_deals_get(request.history_start, request.history_end), 16):
        kind = enum(row, "type", DEAL_KINDS)
        money = dict(
            ticket=row.ticket,
            event_ref=event_ref(scope, row.ticket),
            order_ticket=integer(row, "order"),
            position_identifier=integer(row, "position_id"),
            observed_at=timestamp(row, "time_msc"),
            currency=request.connection.expected_currency,
            **{field: number(row, field) for field in ("profit", "commission", "swap", "fee")},
        )
        if not request.history_start <= money["observed_at"] <= request.history_end:
            raise SyncWithheld("HISTORY_WINDOW")
        if kind in ("BUY", "SELL"):
            quote = mapped(row, quotes)
            if money["order_ticket"] <= 0 or money["position_identifier"] <= 0:
                raise SyncWithheld("DATA_INVALID")
            lots = number(row, "volume")
            fills.append(
                FillView(
                    **money,
                    instrument_id=quote.instrument_id,
                    venue_symbol=quote.venue_symbol,
                    side=kind,
                    entry=enum(row, "entry", ENTRY_KINDS),
                    volume_lots=lots,
                    price=number(row, "price"),
                    quantity_units=lots * quote.contract_size,
                )
            )
        else:
            if getattr(row, "symbol"):
                mapped(row, quotes)
            cash.append(CashEvent(**money, kind=kind))
    return tuple(sorted(fills, key=lambda d: (d.observed_at, d.ticket))), tuple(
        sorted(cash, key=lambda d: (d.observed_at, d.ticket))
    )


def capture_sync(request: DemoSyncRequest, native: SyncSdk, *, clock) -> DemoSyncReport:
    if not request.enabled:
        return DemoSyncReport(state="DISABLED", code="DISABLED")
    if getattr(native, "__version__", None) != request.connection.sdk_version:
        return DemoSyncReport(state="WITHHELD", code="SDK_VERSION")
    result = DemoSyncReport(state="WITHHELD", code="NATIVE_FAILURE")
    try:
        started = utc(clock())
        if request.history_end > started:
            raise SyncWithheld("HISTORY_WINDOW")
        account = read_account(open_demo(request.connection, native))
        quotes = tuple(read_quote(s, native) for s in request.connection.symbols)
        mapping = {q.venue_symbol: q for q in quotes}
        positions = read_positions(native, mapping, account.currency)
        orders = read_orders(native, mapping)
        scope = account_scope(request.connection)
        fills, cash = read_deals(request, native, mapping, scope)
        account_matches(request.connection, native)
        ended = utc(clock())
        if ended < started or ended - started > timedelta(seconds=5):
            raise Withheld("CLOCK")
        if any(
            q.observed_at > ended or ended - q.observed_at >= timedelta(seconds=3) for q in quotes
        ):
            raise Withheld("QUOTE")
        if any(p.created_at > p.updated_at or p.updated_at > ended for p in positions) or any(
            o.created_at > ended for o in orders
        ):
            raise SyncWithheld("DATA_INVALID")
        batch = SyncBatch(
            enrollment_id=request.connection.enrollment_id,
            sdk_version=request.connection.sdk_version,
            account_scope_ref=scope,
            capture_started_at=started,
            observed_at=ended,
            valid_until=min(q.observed_at + timedelta(seconds=3) for q in quotes),
            history_start=request.history_start,
            history_end=request.history_end,
            account=account,
            quotes=quotes,
            positions=positions,
            orders=orders,
            fills=fills,
            cash_events=cash,
        )
        result = DemoSyncReport(state="CAPTURED", code=None, batch=batch)
        if len(result.model_dump_json().encode()) > 12000:
            raise SyncWithheld("RESULT_LIMIT")
    except (Withheld, SyncWithheld) as exc:
        result = DemoSyncReport(state="WITHHELD", code=exc.code)
    except Exception:
        result = DemoSyncReport(state="WITHHELD", code="DATA_INVALID")
    finally:
        try:
            native.shutdown()
        except Exception:
            result = DemoSyncReport(state="WITHHELD", code="CLEANUP_FAILURE")
    return result

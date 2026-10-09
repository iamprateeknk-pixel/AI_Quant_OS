"""Read-only demo observation boundary. Native SDK injection requires worker custody.

This module never imports the terminal SDK or participates in order routing.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import PureWindowsPath
from typing import Callable, Literal, Protocol
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    UUID4,
    field_validator,
    model_validator,
)


class ObservationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DemoSymbol(ObservationModel):
    instrument_id: Literal["EURUSD", "GBPUSD", "USDJPY", "EURJPY"]
    venue_symbol: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")


class DemoCaptureRequest(ObservationModel):
    enabled: bool = Field(default=False, strict=True)
    connection_ack: Literal["CONNECT_DECLARED_DEMO_ONLY"]
    enrollment_id: UUID4
    terminal_path: str = Field(repr=False, exclude=True)
    sdk_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    expected_login: int = Field(gt=0, strict=True, repr=False, exclude=True)
    expected_server: SecretStr
    expected_company: str = Field(min_length=1, max_length=128)
    expected_currency: str = Field(pattern=r"^[A-Z]{3}$")
    password: SecretStr
    symbols: tuple[DemoSymbol, ...] = Field(min_length=1, max_length=4)

    @field_validator("terminal_path")
    @classmethod
    def local_terminal(cls, value: str) -> str:
        path = PureWindowsPath(value)
        if (
            not path.is_absolute()
            or len(path.drive) != 2
            or ".." in path.parts
            or path.suffix.lower() != ".exe"
        ):
            raise ValueError("An absolute local executable path is required")
        return value

    @model_validator(mode="after")
    def enrollment(self):
        if not self.expected_company.startswith("Exness"):
            raise ValueError("Exness enrollment required")
        if not self.password.get_secret_value() or not self.expected_server.get_secret_value():
            raise ValueError("Explicit connection credentials required")
        if len({s.instrument_id for s in self.symbols}) != len(self.symbols) or len(
            {s.venue_symbol for s in self.symbols}
        ) != len(self.symbols):
            raise ValueError("Symbol mapping must be one-to-one")
        return self


class DemoQuote(ObservationModel):
    instrument_id: str
    venue_symbol: str
    bid: Decimal = Field(gt=0, allow_inf_nan=False)
    ask: Decimal = Field(gt=0, allow_inf_nan=False)
    observed_at: datetime
    contract_size: Decimal = Field(gt=0, allow_inf_nan=False)
    digits: int = Field(ge=0, le=12, strict=True)
    volume_min: Decimal = Field(gt=0, allow_inf_nan=False)
    volume_max: Decimal = Field(gt=0, allow_inf_nan=False)
    volume_step: Decimal = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def market_bounds(self):
        if (
            self.ask < self.bid
            or self.volume_max < self.volume_min
            or self.volume_step > self.volume_max
        ):
            raise ValueError("Invalid market bounds")
        utc(self.observed_at)
        return self


class DemoSnapshot(ObservationModel):
    enrollment_id: UUID4
    sdk_version: str
    capture_started_at: datetime
    observed_at: datetime
    valid_until: datetime
    quotes: tuple[DemoQuote, ...]


class DemoCaptureReport(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
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
        ]
        | None
    )
    snapshot: DemoSnapshot | None = None
    trace_id: UUID4 = Field(default_factory=uuid4)
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"


class DemoSdk(Protocol):
    __version__: str

    def initialize(self, path: str, **kwargs: object) -> bool: ...
    def terminal_info(self) -> object: ...
    def account_info(self) -> object: ...
    def symbol_info(self, symbol: str) -> object: ...
    def symbol_info_tick(self, symbol: str) -> object: ...
    def shutdown(self) -> None: ...


class Withheld(Exception):
    def __init__(self, code):
        self.code = code


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise Withheld("CLOCK")
    return value


def account_matches(request: DemoCaptureRequest, native: DemoSdk) -> None:
    terminal = native.terminal_info()
    account = native.account_info()
    if getattr(terminal, "connected", None) is not True:
        raise Withheld("CONNECTION")
    if (
        type(getattr(account, "trade_mode", None)) is not int
        or account.trade_mode != 0
        or type(getattr(account, "login", None)) is not int
        or account.login != request.expected_login
        or getattr(account, "server", None) != request.expected_server.get_secret_value()
        or getattr(account, "company", None) != request.expected_company
        or getattr(account, "currency", None) != request.expected_currency
    ):
        raise Withheld("ACCOUNT")


def read_quote(symbol: DemoSymbol, native: DemoSdk) -> DemoQuote:
    info = native.symbol_info(symbol.venue_symbol)
    tick = native.symbol_info_tick(symbol.venue_symbol)
    if (
        getattr(info, "name", None) != symbol.venue_symbol
        or getattr(info, "currency_base", None) != symbol.instrument_id[:3]
        or getattr(info, "currency_profit", None) != symbol.instrument_id[3:]
        or type(getattr(tick, "time_msc", None)) is not int
        or tick.time_msc <= 0
    ):
        raise Withheld("QUOTE")
    try:
        return DemoQuote(
            instrument_id=symbol.instrument_id,
            venue_symbol=symbol.venue_symbol,
            bid=Decimal(str(tick.bid)),
            ask=Decimal(str(tick.ask)),
            observed_at=datetime(1970, 1, 1, tzinfo=timezone.utc)
            + timedelta(milliseconds=tick.time_msc),
            contract_size=Decimal(str(info.trade_contract_size)),
            digits=info.digits,
            volume_min=Decimal(str(info.volume_min)),
            volume_max=Decimal(str(info.volume_max)),
            volume_step=Decimal(str(info.volume_step)),
        )
    except Exception:
        raise Withheld("QUOTE") from None


def capture_demo(
    request: DemoCaptureRequest, native: DemoSdk, *, clock: Callable[[], datetime]
) -> DemoCaptureReport:
    """Capture from an injected SDK; a caller must supply bounded isolated execution.

    SDK shutdown disconnects this client; it does not prove terminal termination.
    Exceptions are deliberately projected to fixed codes, never SDK error strings.
    """
    if not request.enabled:
        return DemoCaptureReport(state="DISABLED", code="DISABLED")
    if getattr(native, "__version__", None) != request.sdk_version:
        return DemoCaptureReport(state="WITHHELD", code="SDK_VERSION")
    result = DemoCaptureReport(state="WITHHELD", code="NATIVE_FAILURE")
    try:
        started = utc(clock())
        if (
            native.initialize(
                request.terminal_path,
                login=request.expected_login,
                password=request.password.get_secret_value(),
                server=request.expected_server.get_secret_value(),
                timeout=3000,
                portable=False,
            )
            is not True
        ):
            raise Withheld("CONNECTION")
        account_matches(request, native)
        quotes = tuple(read_quote(symbol, native) for symbol in request.symbols)
        account_matches(request, native)
        ended = utc(clock())
        if ended < started or ended - started > timedelta(seconds=5):
            raise Withheld("CLOCK")
        if any(
            q.observed_at > ended or ended - q.observed_at >= timedelta(seconds=3) for q in quotes
        ):
            raise Withheld("QUOTE")
        snapshot = DemoSnapshot(
            enrollment_id=request.enrollment_id,
            sdk_version=request.sdk_version,
            capture_started_at=started,
            observed_at=ended,
            valid_until=min(q.observed_at + timedelta(seconds=3) for q in quotes),
            quotes=quotes,
        )
        result = DemoCaptureReport(state="CAPTURED", code=None, snapshot=snapshot)
    except Withheld as exc:
        result = DemoCaptureReport(state="WITHHELD", code=exc.code)
    except Exception:
        result = DemoCaptureReport(state="WITHHELD", code="NATIVE_FAILURE")
    finally:
        try:
            native.shutdown()
        except Exception:
            result = DemoCaptureReport(state="WITHHELD", code="CLEANUP_FAILURE")
    return result

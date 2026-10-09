import importlib.util
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_demo_observation") is not None
    from ai_quant_os import mt5_demo_observation

    return mt5_demo_observation


def request(enabled=True):
    return implementation().DemoCaptureRequest(
        enabled=enabled,
        connection_ack="CONNECT_DECLARED_DEMO_ONLY",
        enrollment_id="6e320dae-472a-4c63-9168-40291982284c",
        terminal_path="C:/fixture/terminal64.exe",
        sdk_version="5.0.1",
        expected_login=610001,
        expected_server="fixture-demo-server",
        expected_company="Exness fixture",
        expected_currency="USD",
        password="fixture-demo-read",
        symbols=[{"instrument_id": "EURUSD", "venue_symbol": "EURUSDm"}],
    )


class NativeFixture:
    __version__ = "5.0.1"

    def __init__(self):
        self.calls = []
        self.account = SimpleNamespace(
            login=610001,
            server="fixture-demo-server",
            company="Exness fixture",
            currency="USD",
            trade_mode=0,
            name="private-name",
        )
        self.tick = SimpleNamespace(time_msc=int(NOW.timestamp() * 1000), bid=1.1001, ask=1.1002)
        self.info = SimpleNamespace(
            name="EURUSDm",
            currency_base="EUR",
            currency_profit="USD",
            trade_contract_size=100000.0,
            digits=5,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )

    def initialize(self, *args, **kwargs):
        self.calls.append(("initialize", args, kwargs))
        return True

    def terminal_info(self):
        self.calls.append(("terminal",))
        return SimpleNamespace(connected=True)

    def account_info(self):
        self.calls.append(("account",))
        return self.account

    def symbol_info(self, symbol):
        self.calls.append(("symbol", symbol))
        return self.info

    def symbol_info_tick(self, symbol):
        self.calls.append(("tick", symbol))
        return self.tick

    def shutdown(self):
        self.calls.append(("shutdown",))

    def order_send(self, *args, **kwargs):
        raise AssertionError("Observation must never submit an order")


def test_disabled_probe_makes_no_native_calls():
    native = NativeFixture()
    result = implementation().capture_demo(request(False), native, clock=lambda: NOW)
    assert result.state == "DISABLED" and result.snapshot is None
    assert native.calls == []


def test_admitted_demo_capture_normalizes_actual_symbol_facts_without_credentials():
    native = NativeFixture()
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.state == "CAPTURED" and result.code is None
    assert result.may_submit_orders is False and result.may_affect_portfolio is False
    quote = result.snapshot.quotes[0]
    assert quote.instrument_id == "EURUSD" and quote.venue_symbol == "EURUSDm"
    assert str(quote.bid) == "1.1001" and str(quote.contract_size) == "100000.0"
    assert quote.observed_at == NOW
    assert native.calls[-1] == ("shutdown",)
    assert sum(call[0] == "account" for call in native.calls) == 2
    assert native.calls[0][2]["timeout"] == 3000
    raw = result.model_dump_json()
    for private in [
        "fixture-demo-read",
        "fixture-demo-server",
        "private-name",
        "610001",
        "terminal64.exe",
    ]:
        assert private not in raw


@pytest.mark.parametrize(
    "field,value",
    [
        ("trade_mode", 2),
        ("trade_mode", 1),
        ("trade_mode", False),
        ("login", 999999),
        ("server", "other-server"),
        ("company", "other-company"),
        ("currency", "EUR"),
    ],
)
def test_nonmatching_or_nondemo_account_withholds_quotes(field, value):
    native = NativeFixture()
    setattr(native.account, field, value)
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.state == "WITHHELD" and result.snapshot is None
    assert not any(call[0] in {"symbol", "tick"} for call in native.calls)
    assert native.calls[-1] == ("shutdown",)


@pytest.mark.parametrize("milliseconds", [-3000, -3001, 1])
def test_stale_or_future_tick_is_withheld(milliseconds):
    native = NativeFixture()
    native.tick.time_msc += milliseconds
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "QUOTE" and result.snapshot is None


def test_account_switch_after_quotes_discards_capture():
    native = NativeFixture()
    read = native.symbol_info_tick

    def switch(symbol):
        tick = read(symbol)
        native.account.login = 999999
        return tick

    native.symbol_info_tick = switch
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "ACCOUNT" and result.snapshot is None
    assert native.calls[-1] == ("shutdown",)


def test_version_mismatch_does_not_connect():
    native = NativeFixture()
    native.__version__ = "5.0.2"
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "SDK_VERSION" and native.calls == []


@pytest.mark.parametrize("field,value", [("bid", float("nan")), ("ask", 1.0), ("time_msc", True)])
def test_invalid_quote_is_withheld(field, value):
    native = NativeFixture()
    setattr(native.tick, field, value)
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "QUOTE" and result.snapshot is None


def test_connection_exception_is_sanitized_and_cleanup_attempted():
    native = NativeFixture()

    def fail(*args, **kwargs):
        raise RuntimeError("fixture-demo-read fixture-demo-server private-name")

    native.initialize = fail
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "NATIVE_FAILURE" and result.snapshot is None
    assert "fixture-demo" not in result.model_dump_json()
    assert native.calls == [("shutdown",)]


def test_cleanup_failure_discards_successful_observation():
    native = NativeFixture()

    def fail():
        raise RuntimeError("private-name")

    native.shutdown = fail
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "CLEANUP_FAILURE" and result.snapshot is None


@pytest.mark.parametrize("elapsed", [-1, 6])
def test_regressing_or_excessive_capture_clock_is_withheld(elapsed):
    times = iter([NOW, NOW + timedelta(seconds=elapsed)])
    result = implementation().capture_demo(request(), NativeFixture(), clock=lambda: next(times))
    assert result.code == "CLOCK" and result.snapshot is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("terminal_path", "//server/share/terminal.exe"),
        ("terminal_path", "terminal.exe"),
        ("terminal_path", "C:/fixture/../terminal.exe"),
        ("expected_login", True),
        ("enabled", "true"),
        ("expected_company", "Other"),
        ("order_send", True),
    ],
)
def test_enrollment_rejects_ambiguous_or_extra_authority(field, value):
    from pydantic import ValidationError

    baseline = request()
    data = {name: getattr(baseline, name) for name in type(baseline).model_fields}
    data[field] = value
    with pytest.raises(ValidationError):
        implementation().DemoCaptureRequest.model_validate(data)


def test_duplicate_mapping_is_rejected():
    from pydantic import ValidationError

    baseline = request()
    data = {name: getattr(baseline, name) for name in type(baseline).model_fields}
    data["symbols"] = [baseline.symbols[0], baseline.symbols[0]]
    with pytest.raises(ValidationError):
        implementation().DemoCaptureRequest.model_validate(data)


def test_missing_sdk_version_is_withheld_before_connection():
    native = NativeFixture()
    native.__version__ = None
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "SDK_VERSION" and native.calls == []


def test_failed_connection_is_withheld_and_disconnected():
    native = NativeFixture()
    native.initialize = lambda *args, **kwargs: False
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "CONNECTION" and native.calls == [("shutdown",)]


def test_disconnected_terminal_withholds_quotes():
    native = NativeFixture()
    native.terminal_info = lambda: SimpleNamespace(connected=False)
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "CONNECTION" and result.snapshot is None
    assert not any(call[0] in {"tick", "symbol"} for call in native.calls)


@pytest.mark.parametrize("operation", ["symbol_info", "symbol_info_tick"])
def test_unavailable_symbol_facts_are_withheld(operation):
    native = NativeFixture()
    setattr(native, operation, lambda symbol: None)
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "QUOTE" and result.snapshot is None


def test_symbol_currency_mismatch_is_withheld():
    native = NativeFixture()
    native.info.currency_profit = "EUR"
    result = implementation().capture_demo(request(), native, clock=lambda: NOW)
    assert result.code == "QUOTE" and result.snapshot is None


def test_naive_clock_never_initializes_terminal():
    native = NativeFixture()
    result = implementation().capture_demo(
        request(), native, clock=lambda: NOW.replace(tzinfo=None)
    )
    assert result.code == "CLOCK" and native.calls == [("shutdown",)]

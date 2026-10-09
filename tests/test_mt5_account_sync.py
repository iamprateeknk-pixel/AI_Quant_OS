import importlib.util
from datetime import timedelta
from types import SimpleNamespace

import pytest

from test_mt5_demo_observation import NOW, NativeFixture, request


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_account_sync") is not None
    from ai_quant_os import mt5_account_sync

    return mt5_account_sync


def sync_request(enabled=True):
    return implementation().DemoSyncRequest(
        connection=request(enabled), history_start=NOW - timedelta(hours=1), history_end=NOW
    )


class SyncNative(NativeFixture):
    def __init__(self):
        super().__init__()
        self.account.balance = 10000.0
        self.account.credit = 0.0
        self.account.equity = 10010.0
        self.account.profit = 10.0
        self.account.margin = 100.0
        self.account.margin_free = 9910.0
        self.account.margin_mode = 2
        self.account.leverage = 100
        ms = int(NOW.timestamp() * 1000)
        self.positions = [
            SimpleNamespace(
                ticket=101,
                identifier=91,
                type=0,
                volume=0.02,
                price_open=1.10,
                price_current=1.1001,
                profit=10.0,
                swap=-0.3,
                symbol="EURUSDm",
                time_msc=ms - 100000,
                time_update_msc=ms - 1000,
                comment="private-name",
            )
        ]
        self.orders = [
            SimpleNamespace(
                ticket=201,
                type=3,
                state=1,
                volume_initial=0.03,
                volume_current=0.02,
                price_open=1.11,
                price_stoplimit=0.0,
                symbol="EURUSDm",
                time_setup_msc=ms - 10000,
                comment="private-name",
            )
        ]
        self.deals = [
            SimpleNamespace(
                ticket=301,
                order=200,
                position_id=91,
                type=0,
                entry=0,
                volume=0.02,
                price=1.10,
                profit=0.0,
                commission=-0.4,
                swap=0.0,
                fee=-0.1,
                symbol="EURUSDm",
                time_msc=ms - 2000,
                comment="private-name",
            ),
            SimpleNamespace(
                ticket=302,
                order=0,
                position_id=0,
                type=2,
                entry=0,
                volume=0.0,
                price=0.0,
                profit=1000.0,
                commission=0.0,
                swap=0.0,
                fee=0.0,
                symbol="",
                time_msc=ms - 3000,
                comment="private-name",
            ),
        ]

    def positions_get(self):
        self.calls.append(("positions",))
        return self.positions

    def orders_get(self):
        self.calls.append(("orders",))
        return self.orders

    def history_deals_get(self, start, end):
        self.calls.append(("deals", start, end))
        return self.deals


def test_sync_normalizes_account_positions_orders_trades_and_cash_separately():
    native = SyncNative()
    result = implementation().capture_sync(sync_request(), native, clock=lambda: NOW)
    assert result.state == "CAPTURED"
    batch = result.batch
    assert batch.account.currency == "USD" and str(batch.account.balance) == "10000.0"
    assert batch.account.margin_mode == "HEDGING"
    assert batch.positions[0].side == "BUY" and str(batch.positions[0].quantity_units) == "2000.000"
    assert batch.orders[0].kind == "SELL_LIMIT" and str(batch.orders[0].remaining_lots) == "0.02"
    assert batch.fills[0].side == "BUY" and str(batch.fills[0].commission) == "-0.4"
    assert batch.fills[0].entry == "IN" and batch.fills[0].currency == "USD"
    assert batch.cash_events[0].kind == "BALANCE" and str(batch.cash_events[0].profit) == "1000.0"
    assert (
        batch.consistency == "NON_ATOMIC_SEQUENTIAL_READ"
        and batch.account_freshness == "UNVERIFIED"
    )
    assert not result.may_submit_orders and not result.may_affect_portfolio
    assert native.calls[-1] == ("shutdown",)
    assert sum(c[0] == "account" for c in native.calls) == 2
    raw = result.model_dump_json()
    for secret in [
        "private-name",
        "fixture-demo-read",
        "fixture-demo-server",
        "610001",
        "terminal64.exe",
    ]:
        assert secret not in raw


def test_disabled_sync_makes_no_native_calls():
    native = SyncNative()
    assert (
        implementation().capture_sync(sync_request(False), native, clock=lambda: NOW).state
        == "DISABLED"
    )
    assert native.calls == []


@pytest.mark.parametrize("operation", ["positions_get", "orders_get", "history_deals_get"])
def test_native_failure_is_not_an_empty_collection(operation):
    native = SyncNative()
    setattr(native, operation, lambda *args: None)
    result = implementation().capture_sync(sync_request(), native, clock=lambda: NOW)
    assert result.code == "DATA_UNAVAILABLE" and result.batch is None


def test_unknown_symbol_withholds_whole_account_scope():
    native = SyncNative()
    native.positions[0].symbol = "UNKNOWN"
    result = implementation().capture_sync(sync_request(), native, clock=lambda: NOW)
    assert result.code == "UNMAPPED_SYMBOL" and result.batch is None


def test_overlapping_history_windows_keep_stable_deal_identity():
    module = implementation()
    first = module.capture_sync(sync_request(), SyncNative(), clock=lambda: NOW)
    wider = module.DemoSyncRequest(
        connection=request(), history_start=NOW - timedelta(hours=2), history_end=NOW
    )
    second = module.capture_sync(wider, SyncNative(), clock=lambda: NOW)
    assert first.batch.fills[0].event_ref == second.batch.fills[0].event_ref


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("positions", "type", True),
        ("orders", "type", 8),
        ("deals", "type", 99),
        ("deals", "entry", True),
        ("deals", "price", float("nan")),
    ],
)
def test_invalid_native_record_withholds_complete_batch(target, field, value):
    native = SyncNative()
    setattr(getattr(native, target)[0], field, value)
    result = implementation().capture_sync(sync_request(), native, clock=lambda: NOW)
    assert result.state == "WITHHELD" and result.batch is None


def test_empty_native_collections_are_valid_distinct_from_failure():
    native = SyncNative()
    native.positions, native.orders, native.deals = [], [], []
    result = implementation().capture_sync(sync_request(), native, clock=lambda: NOW)
    assert result.state == "CAPTURED" and result.batch.positions == () and result.batch.fills == ()


@pytest.mark.parametrize("collection", ["positions", "orders", "deals"])
def test_duplicate_or_excessive_records_withhold_without_truncation(collection):
    native = SyncNative()
    records = getattr(native, collection)
    setattr(native, collection, records + [records[0]])
    assert (
        implementation().capture_sync(sync_request(), native, clock=lambda: NOW).state == "WITHHELD"
    )
    setattr(native, collection, [records[0]] * 65)
    assert (
        implementation().capture_sync(sync_request(), native, clock=lambda: NOW).code == "CAPACITY"
    )


def test_account_switch_during_history_read_discards_all_facts():
    native = SyncNative()

    def switch(*args):
        native.account.login = 999999
        return native.deals

    native.history_deals_get = switch
    result = implementation().capture_sync(sync_request(), native, clock=lambda: NOW)
    assert result.code == "ACCOUNT" and result.batch is None and native.calls[-1] == ("shutdown",)


def test_cleanup_failure_discards_account_sync():
    native = SyncNative()

    def fail():
        raise RuntimeError("private-name")

    native.shutdown = fail
    result = implementation().capture_sync(sync_request(), native, clock=lambda: NOW)
    assert result.code == "CLEANUP_FAILURE" and result.batch is None


def test_future_history_window_never_initializes_terminal():
    module = implementation()
    future = module.DemoSyncRequest(
        connection=request(), history_start=NOW, history_end=NOW + timedelta(hours=1)
    )
    native = SyncNative()
    result = module.capture_sync(future, native, clock=lambda: NOW)
    assert result.code == "HISTORY_WINDOW"
    assert not any(c[0] == "initialize" for c in native.calls)


def test_out_of_window_deal_is_not_silently_filtered():
    native = SyncNative()
    native.deals[0].time_msc = int((NOW - timedelta(hours=2)).timestamp() * 1000)
    result = implementation().capture_sync(sync_request(), native, clock=lambda: NOW)
    assert result.code == "HISTORY_WINDOW" and result.batch is None


@pytest.mark.parametrize(
    "start,end", [(NOW, NOW), (NOW, NOW + timedelta(days=2)), (NOW.replace(tzinfo=None), NOW)]
)
def test_history_policy_rejects_unbounded_or_naive_windows(start, end):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        implementation().DemoSyncRequest(connection=request(), history_start=start, history_end=end)

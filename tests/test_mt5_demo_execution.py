import importlib.util
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ai_quant_os.contracts import OrderCommand
from test_mt5_demo_observation import NOW, NativeFixture, request


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_demo_execution") is not None
    from ai_quant_os import mt5_demo_execution

    return mt5_demo_execution


def command(
    *, quantity=Decimal("1000"), key="demo-order-1", time_in_force="IOC", order_id="order-1"
):
    return OrderCommand(
        order_id=order_id,
        idempotency_key=key,
        risk_id="risk-1",
        broker_route="EXNESS_MT5_DEMO",
        instrument_id="EURUSD",
        order_type="MARKET",
        side="BUY",
        quantity=quantity,
        limit_price=None,
        stop_price=None,
        time_in_force=time_in_force,
        expires_at=NOW.replace(year=2027),
    )


def enrollment(*, enabled=True):
    module = implementation()
    return module.DemoOrderEnrollment(
        connection=request(),
        enabled=enabled,
        submission_ack="SUBMIT_EXNESS_DEMO_ONLY" if enabled else None,
        max_order_lots=Decimal("0.10"),
    )


class NativeExecutionFixture(NativeFixture):
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008

    def __init__(self):
        super().__init__()
        self.order_requests = []
        self.checks = []
        self.result = SimpleNamespace(
            retcode=10009, order=5001, deal=6001, volume=0.01, price=1.1002
        )

    def order_check(self, order_request):
        self.checks.append(order_request)
        return SimpleNamespace(retcode=0)

    def order_send(self, order_request):
        self.order_requests.append(order_request)
        return self.result


def test_disabled_enrollment_never_connects_or_submits(tmp_path):
    module = implementation()
    native = NativeExecutionFixture()
    result = module.submit_demo_order(
        enrollment(enabled=False), command(), native, tmp_path / "orders.sqlite3", clock=lambda: NOW
    )
    assert result.state == "DISABLED"
    assert native.calls == []
    assert native.order_requests == []


def test_demo_account_market_order_is_normalized_and_replay_is_idempotent(tmp_path):
    module = implementation()
    database = tmp_path / "orders.sqlite3"
    native = NativeExecutionFixture()
    first = module.submit_demo_order(enrollment(), command(), native, database, clock=lambda: NOW)
    replay_native = NativeExecutionFixture()
    replay = module.submit_demo_order(
        enrollment(), command(), replay_native, database, clock=lambda: NOW
    )
    assert first.state == "ACKNOWLEDGED"
    assert first == replay
    assert first.account_scope_ref
    assert first.broker_order_id == "5001"
    assert len(native.order_requests) == 1
    assert replay_native.calls == []
    assert replay_native.order_requests == []
    assert native.order_requests[0]["symbol"] == "EURUSDm"
    assert native.order_requests[0]["volume"] == pytest.approx(0.01)
    assert native.order_requests[0]["type"] == native.ORDER_TYPE_BUY
    assert native.order_requests[0]["comment"].startswith("AQOS-")
    assert "fixture-demo-read" not in first.model_dump_json()


def test_fok_command_uses_native_fill_or_kill_mode(tmp_path):
    module = implementation()
    native = NativeExecutionFixture()
    native.ORDER_FILLING_FOK = 0
    result = module.submit_demo_order(
        enrollment(),
        command(time_in_force="FOK"),
        native,
        tmp_path / "orders.sqlite3",
        clock=lambda: NOW,
    )
    assert result.state == "ACKNOWLEDGED"
    assert native.order_requests[0]["type_filling"] == native.ORDER_FILLING_FOK


def test_order_expiring_during_final_quote_check_is_not_sent(tmp_path):
    module = implementation()
    native = NativeExecutionFixture()
    expiry = command().expires_at
    clock_values = iter([NOW, NOW, NOW, NOW, expiry])
    result = module.submit_demo_order(
        enrollment(),
        command(),
        native,
        tmp_path / "orders.sqlite3",
        clock=lambda: next(clock_values),
    )
    assert result.state == "WITHHELD" and result.code == "INVALID_COMMAND"
    assert native.order_requests == []


@pytest.mark.parametrize("field,value", [("order_id", "o" * 129), ("key", "k" * 129)])
def test_oversized_command_identifiers_are_rejected_before_reservation(tmp_path, field, value):
    module = implementation()
    native = NativeExecutionFixture()
    invalid = command(**{field: value})
    with pytest.raises(ValueError, match="128"):
        module.submit_demo_order(
            enrollment(), invalid, native, tmp_path / "orders.sqlite3", clock=lambda: NOW
        )
    assert native.calls == []
    assert native.order_requests == []
    store = module.DemoOrderStore(tmp_path / "orders.sqlite3")
    try:
        count = store._connection.execute(
            "SELECT COUNT(*) FROM exness_demo_order_attempts"
        ).fetchone()[0]
        assert count == 0
    finally:
        store.close()


def test_idempotency_key_reuse_with_changed_order_is_rejected(tmp_path):
    module = implementation()
    database = tmp_path / "orders.sqlite3"
    native = NativeExecutionFixture()
    module.submit_demo_order(enrollment(), command(), native, database, clock=lambda: NOW)
    with pytest.raises(ValueError, match="idempotency key reused"):
        module.submit_demo_order(
            enrollment(),
            command(quantity=Decimal("2000")),
            NativeExecutionFixture(),
            database,
            clock=lambda: NOW,
        )
    assert len(native.order_requests) == 1


def test_idempotency_key_cannot_be_replayed_into_a_different_account(tmp_path):
    module = implementation()
    database = tmp_path / "orders.sqlite3"
    native = NativeExecutionFixture()
    module.submit_demo_order(enrollment(), command(), native, database, clock=lambda: NOW)
    other_connection = request().model_copy(update={"expected_login": 610002})
    other_enrollment = module.DemoOrderEnrollment(
        connection=other_connection,
        enabled=True,
        submission_ack="SUBMIT_EXNESS_DEMO_ONLY",
        max_order_lots=Decimal("0.10"),
    )
    with pytest.raises(ValueError, match="idempotency key reused"):
        module.submit_demo_order(
            other_enrollment, command(), NativeExecutionFixture(), database, clock=lambda: NOW
        )


@pytest.mark.parametrize("trade_mode", [1, 2, False, None])
def test_non_demo_account_is_withheld_before_order_check_or_submission(tmp_path, trade_mode):
    module = implementation()
    native = NativeExecutionFixture()
    native.account.trade_mode = trade_mode
    result = module.submit_demo_order(
        enrollment(), command(), native, tmp_path / "orders.sqlite3", clock=lambda: NOW
    )
    assert result.state == "WITHHELD" and result.code == "ACCOUNT"
    assert native.checks == [] and native.order_requests == []


def test_native_timeout_is_unknown_and_retry_does_not_resubmit(tmp_path):
    module = implementation()
    database = tmp_path / "orders.sqlite3"
    first_native = NativeExecutionFixture()

    def timeout(order_request):
        first_native.order_requests.append(order_request)
        raise TimeoutError("private terminal details")

    first_native.order_send = timeout
    first = module.submit_demo_order(
        enrollment(), command(), first_native, database, clock=lambda: NOW
    )
    replay_native = NativeExecutionFixture()
    replay = module.submit_demo_order(
        enrollment(), command(), replay_native, database, clock=lambda: NOW
    )
    assert first.state == "UNKNOWN" and replay == first
    assert len(first_native.order_requests) == 1
    assert replay_native.calls == [] and replay_native.order_requests == []
    assert "private terminal details" not in first.model_dump_json()


def test_process_restart_with_unfinished_reservation_never_retries_submission(tmp_path):
    module = implementation()
    database = tmp_path / "orders.sqlite3"
    command_value = command()
    scope = module.account_scope(enrollment().connection)
    store = module.DemoOrderStore(database)
    assert store.reserve(command_value, NOW, scope) is None
    store.close()
    native = NativeExecutionFixture()
    result = module.submit_demo_order(
        enrollment(), command_value, native, database, clock=lambda: NOW
    )
    assert result.state == "UNKNOWN"
    assert result.account_scope_ref == scope
    assert native.calls == [] and native.order_requests == []


def test_partial_demo_fill_is_reported_as_acknowledged_partial(tmp_path):
    module = implementation()
    native = NativeExecutionFixture()
    native.TRADE_RETCODE_DONE_PARTIAL = 10010
    native.result = SimpleNamespace(
        retcode=10010, order=5002, deal=6002, volume=0.005, price=1.1003
    )
    result = module.submit_demo_order(
        enrollment(), command(), native, tmp_path / "orders.sqlite3", clock=lambda: NOW
    )
    assert result.state == "ACKNOWLEDGED"
    assert result.broker_order_id == "5002" and result.broker_deal_id == "6002"
    assert result.filled_lots == Decimal("0.005")


def test_unaligned_or_over_cap_volume_is_rejected_before_send(tmp_path):
    module = implementation()
    for quantity in (Decimal("1500"), Decimal("11000")):
        native = NativeExecutionFixture()
        result = module.submit_demo_order(
            enrollment(),
            command(quantity=quantity, key=str(quantity)),
            native,
            tmp_path / f"{quantity}.sqlite3",
            clock=lambda: NOW,
        )
        assert result.state == "WITHHELD" and result.code == "VOLUME"
        assert native.order_requests == []


def test_account_switch_during_order_check_is_withheld_and_shutdown(tmp_path):
    module = implementation()
    native = NativeExecutionFixture()

    def account_switch(order_request):
        native.account.login = 610002
        return SimpleNamespace(retcode=0)

    native.order_check = account_switch
    result = module.submit_demo_order(
        enrollment(), command(), native, tmp_path / "orders.sqlite3", clock=lambda: NOW
    )
    assert result.state == "WITHHELD" and result.code == "ACCOUNT"
    assert native.order_requests == []
    assert native.calls[-1] == ("shutdown",)


def test_acknowledged_send_without_a_broker_order_id_stays_unknown(tmp_path):
    module = implementation()
    native = NativeExecutionFixture()
    native.result.order = 0
    result = module.submit_demo_order(
        enrollment(), command(), native, tmp_path / "orders.sqlite3", clock=lambda: NOW
    )
    assert result.state == "UNKNOWN" and result.code == "NATIVE_FAILURE"
    assert len(native.order_requests) == 1


def test_unknown_order_reconciles_only_from_a_unique_exact_broker_history_match(tmp_path):
    module = implementation()
    database = tmp_path / "orders.sqlite3"
    native = NativeExecutionFixture()

    def timeout(order_request):
        native.order_requests.append(order_request)
        raise TimeoutError

    native.order_send = timeout
    uncertain = module.submit_demo_order(
        enrollment(), command(), native, database, clock=lambda: NOW
    )
    native = NativeExecutionFixture()
    digest = __import__("hashlib").sha256(b"demo-order-1").hexdigest()
    native.history_orders_get = lambda start, end: [
        SimpleNamespace(
            ticket=5001,
            state=4,
            magic=int(digest[:8], 16) & 0x7FFFFFFF,
            comment=f"AQOS-{digest[:16]}",
            symbol="EURUSDm",
            type=0,
            volume_initial=0.01,
            setup_time_msc=int(NOW.timestamp() * 1000),
        )
    ]
    native.history_deals_get = lambda *, ticket: [
        SimpleNamespace(
            ticket=6001,
            order=ticket,
            magic=int(digest[:8], 16) & 0x7FFFFFFF,
            comment=f"AQOS-{digest[:16]}",
            symbol="EURUSDm",
            type=0,
            volume=0.01,
            price=1.1002,
        )
    ]

    result = module.reconcile_demo_order(
        enrollment(), command(), native, database, clock=lambda: NOW
    )
    assert uncertain.state == "UNKNOWN"
    assert result.state == "ACKNOWLEDGED"
    assert result.broker_order_id == "5001" and result.broker_deal_id == "6001"
    assert result.filled_lots == Decimal("0.01")
    assert native.order_requests == []


@pytest.mark.parametrize("history_kind", ["unavailable", "empty", "ambiguous"])
def test_unknown_order_stays_unknown_when_history_is_unavailable_or_ambiguous(
    tmp_path, history_kind
):
    module = implementation()
    database = tmp_path / "orders.sqlite3"
    native = NativeExecutionFixture()

    def timeout(order_request):
        raise TimeoutError

    native.order_send = timeout
    module.submit_demo_order(enrollment(), command(), native, database, clock=lambda: NOW)
    native = NativeExecutionFixture()
    digest = __import__("hashlib").sha256(b"demo-order-1").hexdigest()
    exact = SimpleNamespace(
        ticket=5001,
        state=4,
        magic=int(digest[:8], 16) & 0x7FFFFFFF,
        comment=f"AQOS-{digest[:16]}",
        symbol="EURUSDm",
        type=0,
        volume_initial=0.01,
        setup_time_msc=int(NOW.timestamp() * 1000),
    )
    history = {"unavailable": None, "empty": [], "ambiguous": [exact, exact]}[history_kind]
    native.history_orders_get = lambda start, end: history
    result = module.reconcile_demo_order(
        enrollment(), command(), native, database, clock=lambda: NOW
    )
    assert result.state == "UNKNOWN"
    assert result.broker_order_id is None
    assert native.order_requests == []

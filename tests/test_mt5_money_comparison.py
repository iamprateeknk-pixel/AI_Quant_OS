import importlib.util
import json
from copy import copy
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from test_mt5_position_comparison import fixture


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_money_comparison") is not None
    from ai_quant_os import mt5_money_comparison

    return mt5_money_comparison


def declaration(module, store, connection, *, balance="9000.5", credit="0", rules=None):
    view = store.view(connection)
    rules = (
        rules
        if rules is not None
        else [
            module.ComponentAllocation(
                kind=kind, profit="BALANCE", commission="BALANCE", swap="BALANCE", fee="BALANCE"
            )
            for kind in ("BUY", "BALANCE")
        ]
    )
    return module.DeclaredOpeningMoney(
        account_scope_ref=view.batch.account_scope_ref,
        history_start=view.history_start,
        currency="USD",
        balance=balance,
        credit=credit,
        allocations=rules,
    )


def test_reported_profit_commission_swap_fee_reconcile_with_declared_opening(tmp_path):
    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    result = module.compare_money(store, connection, declaration(module, store, connection))
    assert result.state == "CONSISTENT_WITH_DECLARATION"
    assert result.summary.balance_movement == Decimal("999.5")
    assert result.summary.expected_balance == result.summary.observed_balance == Decimal("10000")
    assert result.summary.credit_movement == result.summary.credit_difference == 0
    rows = {r.kind: r for r in result.rows}
    assert rows["BUY"].commission == Decimal("-0.4") and rows["BUY"].fee == Decimal("-0.1")
    assert rows["BALANCE"].profit == Decimal("1000")
    assert (
        result.may_affect_portfolio is False
        and result.allocation_attestation == "DECLARED_UNVERIFIED"
    )


def test_wrong_opening_retains_independent_balance_difference(tmp_path):
    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    result = module.compare_money(
        store, connection, declaration(module, store, connection, balance="9000")
    )
    assert result.state == "DIVERGENT" and result.summary.balance_difference == Decimal("0.5")


def test_credit_profit_and_trade_charges_are_allocated_separately(tmp_path):
    from test_mt5_account_sync import SyncNative

    module = implementation()
    native = SyncNative()
    credit = copy(native.deals[1])
    credit.ticket, credit.type, credit.profit, credit.fee = 303, 3, 50.0, -1.0
    native.deals.append(credit)
    native.account.credit = 50.0
    native.account.balance = 9999.0
    store, connection, _, _, _, _ = fixture(tmp_path, native)
    opening = declaration(module, store, connection)
    rule = module.ComponentAllocation(
        kind="CREDIT", profit="CREDIT", commission="BALANCE", swap="BALANCE", fee="BALANCE"
    )
    opening = opening.model_copy(update={"allocations": (*opening.allocations, rule)})
    result = module.compare_money(store, connection, opening)
    assert result.state == "CONSISTENT_WITH_DECLARATION"
    assert result.summary.credit_movement == Decimal("50")
    assert result.summary.balance_movement == Decimal("998.5")
    row = next(r for r in result.rows if r.kind == "CREDIT")
    assert row.balance_movement == Decimal("-1") and row.credit_movement == Decimal("50")


def test_positive_rebate_and_negative_swap_preserve_reported_signs(tmp_path):
    from test_mt5_account_sync import SyncNative

    module = implementation()
    native = SyncNative()
    native.deals[0].commission, native.deals[0].swap = 0.2, -0.3
    native.account.balance = 10000.3
    store, connection, _, _, _, _ = fixture(tmp_path, native)
    result = module.compare_money(store, connection, declaration(module, store, connection))
    assert result.state == "CONSISTENT_WITH_DECLARATION"
    row = next(r for r in result.rows if r.kind == "BUY")
    assert row.commission == Decimal("0.2") and row.swap == Decimal("-0.3")
    assert row.balance_movement == Decimal("-0.2")


def test_corrected_money_version_replaces_original_once(tmp_path):
    module = implementation()
    store, connection, native, time, _, capture = fixture(tmp_path)
    native.deals[0].commission = -0.7
    native.account.balance = 9999.7
    time[0] += timedelta(seconds=1)
    capture()
    result = module.compare_money(store, connection, declaration(module, store, connection))
    assert result.state == "CONSISTENT_WITH_DECLARATION" and result.revised_deals == 1
    assert result.summary.balance_movement == Decimal("999.2")
    assert sum(r.deals for r in result.rows) == 2


def test_cancelled_trade_is_withheld_even_with_declared_bucket_rule(tmp_path):
    module = implementation()
    store, connection, native, time, _, capture = fixture(tmp_path)
    native.deals[0].type = 13
    time[0] += timedelta(seconds=1)
    capture()
    opening = declaration(module, store, connection)
    rule = module.ComponentAllocation(
        kind="BUY_CANCELED", profit="BALANCE", commission="BALANCE", swap="BALANCE", fee="BALANCE"
    )
    result = module.compare_money(
        store, connection, opening.model_copy(update={"allocations": (*opening.allocations, rule)})
    )
    assert result.code == "CANCELLATION_ACCOUNTING_UNVERIFIED"
    assert result.summary is None and result.rows == () and result.cancelled_deals == 1


def test_missing_prior_deal_withholds_cash_comparison(tmp_path):
    module = implementation()
    store, connection, native, time, _, capture = fixture(tmp_path)
    native.deals = [native.deals[1]]
    time[0] += timedelta(seconds=1)
    capture()
    result = module.compare_money(store, connection, declaration(module, store, connection))
    assert result.code == "MISSING_PRIOR_DEALS" and result.summary is None


def test_unclassified_kind_is_not_assigned_to_balance_automatically(tmp_path):
    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    opening = declaration(module, store, connection)
    result = module.compare_money(
        store, connection, opening.model_copy(update={"allocations": opening.allocations[:1]})
    )
    assert result.code == "UNCLASSIFIED_COMPONENTS" and result.rows == ()


@pytest.mark.parametrize(
    "field,value", [("currency", "EUR"), ("account_scope_ref", "0" * 64), ("history_start", None)]
)
def test_opening_currency_scope_and_start_bind_exact_source(tmp_path, field, value):
    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    opening = declaration(module, store, connection)
    if value is None:
        value = opening.history_start + timedelta(seconds=1)
    result = module.compare_money(store, connection, opening.model_copy(update={field: value}))
    assert result.code == "OPENING_MISMATCH" and result.summary is None


def test_duplicate_allocation_rules_are_rejected(tmp_path):
    from pydantic import ValidationError

    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    rule = module.ComponentAllocation(
        kind="BUY", profit="BALANCE", commission="BALANCE", swap="BALANCE", fee="BALANCE"
    )
    with pytest.raises(ValidationError):
        declaration(module, store, connection, rules=[rule, rule])


def test_caller_precision_does_not_round_money(tmp_path):
    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    opening = declaration(module, store, connection)
    expected = module.compare_money(store, connection, opening)
    with localcontext() as context:
        context.prec = 1
        assert module.compare_money(store, connection, opening) == expected


def test_exported_money_contracts_reject_mutation_authority(tmp_path):
    from pathlib import Path
    from pydantic import TypeAdapter, ValidationError

    module = implementation()
    for name, model in (
        ("demo-opening-money", module.DeclaredOpeningMoney),
        ("demo-money-comparison", module.MoneyComparison),
    ):
        path = Path("docs/schemas") / (name + ".schema.json")
        assert (
            path.is_file()
            and json.loads(path.read_text(encoding="utf-8")) == TypeAdapter(model).json_schema()
        )
    store, connection, _, _, _, _ = fixture(tmp_path)
    result = module.compare_money(store, connection, declaration(module, store, connection))
    data = result.model_dump(mode="json")
    data["may_affect_portfolio"] = True
    with pytest.raises(ValidationError):
        module.MoneyComparison.model_validate(data)


@pytest.mark.parametrize(
    "raw", [b'{"allocations":[],"allocations":[]}', b"x" * 32769], ids=["duplicate", "oversized"]
)
def test_invalid_money_declaration_is_refused_before_state_access(tmp_path, capsys, raw):
    from ai_quant_os import mt5_sync_service

    path = tmp_path / "opening.json"
    path.write_bytes(raw)
    state = tmp_path / "state"
    assert (
        mt5_sync_service.main(
            [
                "--config",
                str(tmp_path / "absent.json"),
                "--state-dir",
                str(state),
                "--compare-money-history",
                str(path),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "COMPARISON_INVALID"
    assert not state.exists()


def test_money_bounds_withhold_without_rounding_or_caller_context_effect(tmp_path):
    from test_mt5_account_sync import SyncNative

    module = implementation()
    native = SyncNative()
    native.deals[0].profit = 1e13
    store, connection, _, _, _, _ = fixture(tmp_path, native)
    result = module.compare_money(store, connection, declaration(module, store, connection))
    assert result.code == "ARITHMETIC_BOUND" and result.summary is None


def test_closed_store_cannot_reopen_or_invent_money(tmp_path):
    module = implementation()
    store, connection, _, _, calls, _ = fixture(tmp_path)
    opening = declaration(module, store, connection)
    store.close()
    result = module.compare_money(store, connection, opening)
    assert result.code == "STORAGE_UNAVAILABLE" and result.summary is None and len(calls) == 1


def test_money_wire_requires_component_conservation_and_exact_summary(tmp_path):
    from pydantic import ValidationError

    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    result = module.compare_money(store, connection, declaration(module, store, connection))
    data = result.model_dump(mode="json")
    data["rows"][0]["balance_movement"] = "12"
    with pytest.raises(ValidationError):
        module.MoneyComparison.model_validate(data)
    data = result.model_dump(mode="json")
    data["summary"]["expected_credit"] = "2"
    with pytest.raises(ValidationError):
        module.MoneyComparison.model_validate(data)
    data = result.model_dump(mode="json")
    data["cancelled_deals"] = 1
    with pytest.raises(ValidationError):
        module.MoneyComparison.model_validate(data)


def test_real_money_cli_keeps_heartbeat_revision_and_gateway_attempts(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    from ai_quant_os.mt5_demo_gateway import DemoObservationGateway
    from ai_quant_os.mt5_sync_controller import DemoSyncController, SyncSchedule
    from test_mt5_account_sync import NOW
    from test_mt5_sync_service import config_document

    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    policy = SyncSchedule(enabled=True, initial_history_start=NOW - timedelta(hours=1))
    gateway = DemoObservationGateway(tmp_path / "gateway.sqlite")
    controller = DemoSyncController(
        tmp_path / "controller.sqlite", connection, policy, gateway, store, clock=lambda: NOW
    )
    before = controller.inspect_health()
    opening = tmp_path / "opening.json"
    opening.write_text(declaration(module, store, connection).model_dump_json(), encoding="utf-8")
    controller.close()
    gateway.close()
    store.close()
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_document()), encoding="utf-8")
    heartbeat = tmp_path / "health.json"
    heartbeat.write_bytes(b"old heartbeat")
    environment = {k: v for k, v in os.environ.items() if k.upper() != "AI_QUANT_MT5_PASSWORD"}
    environment["PYTHONPATH"] = str(Path("src").absolute())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.mt5_sync_service",
            "--config",
            str(config),
            "--state-dir",
            str(tmp_path),
            "--compare-money-history",
            str(opening),
        ],
        capture_output=True,
        env=environment,
        timeout=10,
    )
    assert result.returncode == 0 and result.stderr == b""
    report = module.MoneyComparison.model_validate_json(result.stdout)
    assert (
        report.state == "CONSISTENT_WITH_DECLARATION" and heartbeat.read_bytes() == b"old heartbeat"
    )
    with sqlite3.connect(tmp_path / "gateway.sqlite") as db:
        assert db.execute("SELECT count(*) FROM demo_gateway_attempts").fetchone()[0] == 1
    gateway = DemoObservationGateway(tmp_path / "gateway.sqlite")
    from ai_quant_os.mt5_sync_projection import SyncProjectionStore

    projection = SyncProjectionStore(tmp_path / "projection.sqlite")
    reopened = DemoSyncController(
        tmp_path / "controller.sqlite", connection, policy, gateway, projection, clock=lambda: NOW
    )
    assert reopened.inspect_health() == before
    reopened.close()
    projection.close()
    gateway.close()

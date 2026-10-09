import importlib.util
import json
from copy import copy
from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from test_mt5_account_sync import NOW, SyncNative, sync_request
from test_mt5_sync_projection import fixture_gateway


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_position_comparison") is not None
    from ai_quant_os import mt5_position_comparison

    return mt5_position_comparison


def fixture(tmp_path, native=None):
    from ai_quant_os.mt5_sync_projection import SyncProjectionStore

    native = native or SyncNative()
    time = [NOW]
    request = sync_request()
    gateway, calls = fixture_gateway(tmp_path / "gateway.sqlite", native, time)
    store = SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])

    def capture():
        attempt = uuid4()
        gateway.capture(request, attempt)
        assert store.apply(gateway, request, attempt).state == "APPLIED"

    capture()
    return store, request.connection, native, time, calls, capture


def opening(module, store, connection, positions=()):
    view = store.view(connection)
    return module.DeclaredOpeningPositions(
        account_scope_ref=view.batch.account_scope_ref,
        history_start=view.history_start,
        positions=positions,
    )


def test_explicit_empty_opening_matches_retained_fill_to_observed_position(tmp_path):
    module = implementation()
    store, connection, _, _, calls, _ = fixture(tmp_path)
    result = module.compare_positions(store, connection, opening(module, store, connection))
    assert result.state == "CONSISTENT_WITH_DECLARATION"
    assert result.rows[0].opening_lots == Decimal("0")
    assert result.rows[0].recorded_movement_lots == Decimal("0.02")
    assert result.rows[0].expected_lots == result.rows[0].observed_lots == Decimal("0.02")
    assert result.rows[0].difference_lots == Decimal("0") and len(calls) == 1
    assert result.may_affect_portfolio is False and result.may_submit_orders is False
    assert result.opening_attestation == "DECLARED_UNVERIFIED"


def test_nonzero_opening_is_required_when_window_starts_after_position_open(tmp_path):
    module = implementation()
    native = SyncNative()
    native.deals = [native.deals[1]]
    store, connection, _, _, _, _ = fixture(tmp_path, native)
    empty = module.compare_positions(store, connection, opening(module, store, connection))
    assert empty.state == "DIVERGENT" and empty.rows[0].difference_lots == Decimal("0.02")
    declared = module.OpeningPosition(
        instrument_id="EURUSD", position_identifier=91, signed_lots="0.02"
    )
    result = module.compare_positions(
        store, connection, opening(module, store, connection, (declared,))
    )
    assert result.state == "CONSISTENT_WITH_DECLARATION"
    assert result.rows[0].recorded_movement_lots == 0


def test_partial_close_and_reversal_keep_position_identifier_and_signed_lots(tmp_path):
    module = implementation()
    native = SyncNative()
    entry = native.deals[0]
    close = copy(entry)
    close.ticket, close.type, close.entry, close.volume = 303, 1, 1, 0.01
    reversal = copy(entry)
    reversal.ticket, reversal.type, reversal.entry, reversal.volume = 304, 1, 2, 0.03
    native.deals += [close, reversal]
    native.positions[0].type, native.positions[0].volume = 1, 0.02
    store, connection, _, _, _, _ = fixture(tmp_path, native)
    result = module.compare_positions(store, connection, opening(module, store, connection))
    assert result.state == "CONSISTENT_WITH_DECLARATION"
    assert result.rows[0].recorded_movement_lots == Decimal("-0.02")
    assert result.rows[0].fill_count == 3


def test_hedged_positions_are_compared_separately_even_when_net_lots_are_zero(tmp_path):
    module = implementation()
    native = SyncNative()
    second = copy(native.positions[0])
    second.ticket, second.identifier, second.type = 102, 92, 1
    native.positions.append(second)
    sell = copy(native.deals[0])
    sell.ticket, sell.position_id, sell.type = 303, 92, 1
    native.deals.append(sell)
    store, connection, _, _, _, _ = fixture(tmp_path, native)
    result = module.compare_positions(store, connection, opening(module, store, connection))
    assert result.state == "CONSISTENT_WITH_DECLARATION" and len(result.rows) == 2
    assert [r.expected_lots for r in result.rows] == [Decimal("0.02"), Decimal("-0.02")]


def test_latest_corrected_deal_is_counted_once(tmp_path):
    module = implementation()
    store, connection, native, time, _, capture = fixture(tmp_path)
    native.deals[0].volume = native.positions[0].volume = 0.03
    time[0] += timedelta(seconds=1)
    capture()
    result = module.compare_positions(store, connection, opening(module, store, connection))
    assert result.rows[0].expected_lots == Decimal("0.03") and result.rows[0].fill_count == 1
    assert result.revised_deals == 1 and result.state == "CONSISTENT_WITH_DECLARATION"


def test_cancelled_fill_becomes_cash_and_no_longer_contributes_lots(tmp_path):
    module = implementation()
    store, connection, native, time, _, capture = fixture(tmp_path)
    native.deals[0].type = 13
    native.positions = []
    time[0] += timedelta(seconds=1)
    capture()
    result = module.compare_positions(store, connection, opening(module, store, connection))
    assert result.state == "CONSISTENT_WITH_DECLARATION" and result.rows == ()
    assert result.cancelled_deals == 1 and result.revised_deals == 1


def test_missing_prior_deal_withholds_instead_of_erasing_lots(tmp_path):
    module = implementation()
    store, connection, native, time, _, capture = fixture(tmp_path)
    native.deals = [native.deals[1]]
    time[0] += timedelta(seconds=1)
    capture()
    result = module.compare_positions(store, connection, opening(module, store, connection))
    assert result.state == "WITHHELD" and result.code == "MISSING_PRIOR_DEALS" and result.rows == ()


@pytest.mark.parametrize("field,value", [("account_scope_ref", "0" * 64), ("history_start", NOW)])
def test_opening_scope_and_exact_window_boundary_are_required(tmp_path, field, value):
    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    declaration = opening(module, store, connection).model_copy(update={field: value})
    result = module.compare_positions(store, connection, declaration)
    assert result.state == "WITHHELD" and result.code == "OPENING_MISMATCH"


def test_duplicate_opening_identifiers_are_rejected(tmp_path):
    from pydantic import ValidationError

    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    position = module.OpeningPosition(
        instrument_id="EURUSD", position_identifier=91, signed_lots="0.02"
    )
    with pytest.raises(ValidationError):
        opening(module, store, connection, (position, position))


def test_invalid_native_identity_produces_no_invented_comparison_history(tmp_path):
    from ai_quant_os.mt5_sync_projection import SyncProjectionStore
    from ai_quant_os.mt5_account_sync import account_scope

    module = implementation()
    native = SyncNative()
    native.deals[0].position_id = 0
    request = sync_request()
    gateway, _ = fixture_gateway(tmp_path / "gateway.sqlite", native, [NOW])
    assert gateway.capture(request, uuid4()).report.code == "DATA_INVALID"
    store = SyncProjectionStore(tmp_path / "projection.sqlite")
    declaration = module.DeclaredOpeningPositions(
        account_scope_ref=account_scope(request.connection),
        history_start=request.history_start,
        positions=(),
    )
    result = module.compare_positions(store, request.connection, declaration)
    assert result.code == "NO_HISTORY" and result.rows == ()


def test_caller_decimal_precision_does_not_change_comparison(tmp_path):
    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    declaration = opening(module, store, connection)
    expected = module.compare_positions(store, connection, declaration)
    with localcontext() as context:
        context.prec = 1
        result = module.compare_positions(store, connection, declaration)
    assert result == expected


def test_closed_store_withholds_without_reopening_or_dispatch(tmp_path):
    module = implementation()
    store, connection, _, _, calls, _ = fixture(tmp_path)
    declaration = opening(module, store, connection)
    store.close()
    result = module.compare_positions(store, connection, declaration)
    assert result.code == "STORAGE_UNAVAILABLE" and result.rows == () and len(calls) == 1


def test_large_native_lots_are_withheld_without_rounded_comparison(tmp_path):
    module = implementation()
    native = SyncNative()
    native.positions[0].volume = native.deals[0].volume = 1e10
    store, connection, _, _, _, _ = fixture(tmp_path, native)
    result = module.compare_positions(store, connection, opening(module, store, connection))
    assert result.code == "ARITHMETIC_BOUND" and result.rows == ()


def test_unmapped_opening_instrument_is_withheld(tmp_path):
    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    unknown = module.OpeningPosition(
        instrument_id="UNKNOWN", position_identifier=92, signed_lots="0.01"
    )
    result = module.compare_positions(
        store, connection, opening(module, store, connection, (unknown,))
    )
    assert result.code == "UNMAPPED_INSTRUMENT" and result.rows == ()


def test_changed_connection_policy_cannot_read_original_comparison(tmp_path):
    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    declaration = opening(module, store, connection)
    changed = connection.model_copy(update={"enrollment_id": uuid4()})
    result = module.compare_positions(store, changed, declaration)
    assert result.code == "SOURCE_INVALID" and result.rows == ()


@pytest.mark.parametrize(
    "raw", [b'{"positions":[],"positions":[]}', b"x" * 32769], ids=["duplicate", "oversized"]
)
def test_invalid_opening_cli_is_refused_before_state_creation(tmp_path, capsys, raw):
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
                "--compare-position-history",
                str(path),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "COMPARISON_INVALID"
    assert not state.exists()


def test_comparison_contract_rejects_authority_and_inconsistent_claims(tmp_path):
    from pydantic import ValidationError

    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    result = module.compare_positions(store, connection, opening(module, store, connection))
    data = result.model_dump(mode="json")
    data["may_affect_portfolio"] = True
    with pytest.raises(ValidationError):
        module.PositionComparison.model_validate(data)
    data["may_affect_portfolio"] = False
    data["rows"][0]["difference_lots"] = "0.01"
    with pytest.raises(ValidationError):
        module.PositionComparison.model_validate(data)


def test_opening_and_comparison_schemas_are_exported(tmp_path):
    from pathlib import Path
    from pydantic import TypeAdapter

    module = implementation()
    for name, model in (
        ("demo-opening-positions", module.DeclaredOpeningPositions),
        ("demo-position-comparison", module.PositionComparison),
    ):
        path = Path("docs/schemas") / (name + ".schema.json")
        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8")) == TypeAdapter(model).json_schema()


def test_comparison_cli_is_offline_and_keeps_existing_heartbeat(tmp_path, monkeypatch, capsys):
    from ai_quant_os import mt5_sync_service
    from ai_quant_os.mt5_sync_controller import DemoSyncController, SyncSchedule
    from test_mt5_sync_service import config_document

    module = implementation()
    store, connection, _, _, calls, _ = fixture(tmp_path)
    policy = SyncSchedule(enabled=True, initial_history_start=NOW - timedelta(hours=1))
    gateway, _ = fixture_gateway(tmp_path / "gateway.sqlite", SyncNative(), [NOW])
    controller = DemoSyncController(
        tmp_path / "controller.sqlite", connection, policy, gateway, store, clock=lambda: NOW
    )
    controller.close()
    gateway.close()
    declaration = opening(module, store, connection)
    baseline = tmp_path / "opening.json"
    baseline.write_text(declaration.model_dump_json(), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_document()), encoding="utf-8")
    marker = tmp_path / "health.json"
    marker.write_bytes(b"old heartbeat")
    monkeypatch.delenv("AI_QUANT_MT5_PASSWORD", raising=False)
    monkeypatch.setattr(mt5_sync_service.sys, "platform", "linux")

    def forbidden(*args, **kwargs):
        raise AssertionError("Comparison must not dispatch or run a timer")

    monkeypatch.setattr(mt5_sync_service, "run_service", forbidden)
    monkeypatch.setattr(mt5_sync_service.DemoObservationGateway, "capture", forbidden)
    assert (
        mt5_sync_service.main(
            [
                "--config",
                str(config),
                "--state-dir",
                str(tmp_path),
                "--compare-position-history",
                str(baseline),
            ]
        )
        == 0
    )
    result = module.PositionComparison.model_validate_json(capsys.readouterr().out)
    assert result.state == "CONSISTENT_WITH_DECLARATION" and len(calls) == 1
    assert marker.read_bytes() == b"old heartbeat"


def test_real_comparison_command_uses_existing_history_without_new_attempts(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    from ai_quant_os.mt5_demo_gateway import DemoObservationGateway
    from ai_quant_os.mt5_sync_controller import DemoSyncController, SyncSchedule
    from test_mt5_sync_service import config_document

    module = implementation()
    store, connection, _, _, _, _ = fixture(tmp_path)
    policy = SyncSchedule(enabled=True, initial_history_start=NOW - timedelta(hours=1))
    gateway = DemoObservationGateway(tmp_path / "gateway.sqlite")
    controller = DemoSyncController(
        tmp_path / "controller.sqlite", connection, policy, gateway, store, clock=lambda: NOW
    )
    opening_file = tmp_path / "opening.json"
    opening_file.write_text(opening(module, store, connection).model_dump_json(), encoding="utf-8")
    controller.close()
    gateway.close()
    store.close()
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_document()), encoding="utf-8")
    environment = {k: v for k, v in os.environ.items() if k.upper() != "AI_QUANT_MT5_PASSWORD"}
    environment["PYTHONPATH"] = str(Path("src").absolute())
    command = [
        sys.executable,
        "-m",
        "ai_quant_os.mt5_sync_service",
        "--config",
        str(config),
        "--state-dir",
        str(tmp_path),
        "--compare-position-history",
        str(opening_file),
    ]
    completed = subprocess.run(command, capture_output=True, env=environment, timeout=10)
    assert completed.returncode == 0 and completed.stderr == b""
    result = module.PositionComparison.model_validate_json(completed.stdout)
    assert result.state == "CONSISTENT_WITH_DECLARATION"
    with sqlite3.connect(tmp_path / "gateway.sqlite") as db:
        assert db.execute("SELECT count(*) FROM demo_gateway_attempts").fetchone()[0] == 1
    assert not (tmp_path / "health.json").exists()

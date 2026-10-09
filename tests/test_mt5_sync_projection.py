import importlib.util
import sqlite3
from datetime import timedelta
from uuid import uuid4

from ai_quant_os.mt5_demo_gateway import DemoObservationGateway
from ai_quant_os.mt5_demo_worker import SupervisedSync
from test_mt5_account_sync import NOW, SyncNative, implementation as sync_module, sync_request


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_sync_projection") is not None
    from ai_quant_os import mt5_sync_projection

    return mt5_sync_projection


def fixture_gateway(path, native, time):
    calls = []

    def run(request, *, clock, worker_id):
        calls.append(worker_id)
        return SupervisedSync(
            state="RETURNED",
            worker_id=worker_id,
            worker_custody="EXIT_CONFIRMED",
            worker_reply_code="OK",
            report=sync_module().capture_sync(request, native, clock=clock),
        )

    return DemoObservationGateway(path, runner=run, clock=lambda: time[0]), calls


def test_projection_applies_verified_receipt_once_and_survives_restart(tmp_path):
    module = implementation()
    time = [NOW]
    gateway, calls = fixture_gateway(tmp_path / "gateway.sqlite", SyncNative(), time)
    request = sync_request()
    attempt = uuid4()
    receipt = gateway.capture(request, attempt)
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])
    applied = store.apply(gateway, request, attempt)
    assert applied.state == "APPLIED" and applied.new_deals == 2 and applied.revised_deals == 0
    assert store.apply(gateway, request, attempt).state == "ALREADY_APPLIED"
    assert len(calls) == 1
    view = store.view(request.connection)
    assert (
        view.latest_receipt_ref == receipt.receipt_ref
        and view.known_deals == 2
        and view.revision_count == 0
    )
    assert view.batch.account.balance == receipt.report.batch.account.balance
    store.close()
    restarted = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])
    assert restarted.view(request.connection) == view


def test_missing_gateway_attempt_never_dispatches_native_capture(tmp_path):
    module = implementation()
    time = [NOW]
    gateway, calls = fixture_gateway(tmp_path / "gateway.sqlite", SyncNative(), time)
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: NOW)
    result = store.apply(gateway, sync_request(), uuid4())
    assert result.code == "SOURCE_WITHHELD" and calls == []


def test_overlapping_window_retains_changed_deal_revision_without_duplicate_identity(tmp_path):
    module = implementation()
    time = [NOW]
    native = SyncNative()
    gateway, calls = fixture_gateway(tmp_path / "gateway.sqlite", native, time)
    request = sync_request()
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])
    first = uuid4()
    first_receipt = gateway.capture(request, first)
    store.apply(gateway, request, first)
    native.deals[0].commission = -0.7
    time[0] = NOW + timedelta(seconds=1)
    second = uuid4()
    gateway.capture(request, second)
    applied = store.apply(gateway, request, second)
    assert applied.new_deals == 0 and applied.revised_deals == 1
    ref = first_receipt.report.batch.fills[0].event_ref
    versions = store.deal_versions(request.connection, ref)
    assert [str(v.payload.commission) for v in versions] == ["-0.4", "-0.7"]
    assert store.view(request.connection).known_deals == 2 and len(calls) == 2


def test_missing_prior_deal_is_marked_without_erasing_history(tmp_path):
    module = implementation()
    time = [NOW]
    native = SyncNative()
    gateway, _ = fixture_gateway(tmp_path / "gateway.sqlite", native, time)
    request = sync_request()
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])
    first = uuid4()
    receipt = gateway.capture(request, first)
    store.apply(gateway, request, first)
    ref = receipt.report.batch.fills[0].event_ref
    native.deals = [native.deals[1]]
    time[0] += timedelta(seconds=1)
    second = uuid4()
    gateway.capture(request, second)
    store.apply(gateway, request, second)
    assert store.view(request.connection).missing_prior_deals == (ref,)
    assert len(store.deal_versions(request.connection, ref)) == 1


def test_corrupt_projection_never_imports_another_receipt(tmp_path):
    module = implementation()
    time = [NOW]
    gateway, _ = fixture_gateway(tmp_path / "gateway.sqlite", SyncNative(), time)
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: NOW)
    first = uuid4()
    gateway.capture(sync_request(), first)
    store.apply(gateway, sync_request(), first)
    store.close()
    with sqlite3.connect(tmp_path / "projection.sqlite") as connection:
        connection.execute("DELETE FROM sync_projection_receipts")
    reopened = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: NOW)
    assert reopened.apply(gateway, sync_request(), first).code == "STORAGE_UNAVAILABLE"


def test_same_attempt_from_another_journal_cannot_alias_an_imported_receipt(tmp_path):
    module = implementation()
    time = [NOW]
    first, _ = fixture_gateway(tmp_path / "gateway-one.sqlite", SyncNative(), time)
    second, _ = fixture_gateway(tmp_path / "gateway-two.sqlite", SyncNative(), time)
    attempt = uuid4()
    request = sync_request()
    first.capture(request, attempt)
    second.capture(request, attempt)
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: NOW)
    assert store.apply(first, request, attempt).state == "APPLIED"
    assert store.apply(second, request, attempt).code == "ATTEMPT_CONFLICT"


def test_repeat_import_after_expiry_reads_only_receipt_commitment(tmp_path):
    module = implementation()
    time = [NOW]
    gateway, calls = fixture_gateway(tmp_path / "gateway.sqlite", SyncNative(), time)
    request = sync_request()
    attempt = uuid4()
    gateway.capture(request, attempt)
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])
    store.apply(gateway, request, attempt)
    time[0] += timedelta(seconds=4)
    assert store.apply(gateway, request, attempt).state == "ALREADY_APPLIED"
    assert store.view(request.connection).quote_context == "STALE" and len(calls) == 1


def test_disconnected_backfill_does_not_claim_contiguous_history(tmp_path):
    module = implementation()
    time = [NOW]
    native = SyncNative()
    gateway, _ = fixture_gateway(tmp_path / "gateway.sqlite", native, time)
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])
    request = sync_request()
    first = uuid4()
    gateway.capture(request, first)
    store.apply(gateway, request, first)
    time[0] += timedelta(seconds=1)
    native.deals = []
    backfill = sync_module().DemoSyncRequest(
        connection=request.connection,
        history_start=NOW - timedelta(hours=4),
        history_end=NOW - timedelta(hours=3),
    )
    second = uuid4()
    gateway.capture(backfill, second)
    assert store.apply(gateway, backfill, second).code == "HISTORY_GAP"
    assert store.view(request.connection).receipts == 1


def test_cash_correction_reclassifies_observation_without_erasing_trade(tmp_path):
    module = implementation()
    time = [NOW]
    native = SyncNative()
    gateway, _ = fixture_gateway(tmp_path / "gateway.sqlite", native, time)
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])
    request = sync_request()
    first = uuid4()
    receipt = gateway.capture(request, first)
    store.apply(gateway, request, first)
    ref = receipt.report.batch.fills[0].event_ref
    native.deals[0].type = 13
    time[0] += timedelta(seconds=1)
    second = uuid4()
    gateway.capture(request, second)
    assert store.apply(gateway, request, second).revised_deals == 1
    versions = store.deal_versions(request.connection, ref)
    assert [v.kind for v in versions] == ["FILL", "CASH"] and versions[
        -1
    ].payload.kind == "BUY_CANCELED"


def test_equal_capture_time_cannot_arbitrarily_replace_current_state(tmp_path):
    module = implementation()
    time = [NOW]
    native = SyncNative()
    gateway, _ = fixture_gateway(tmp_path / "gateway.sqlite", native, time)
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: NOW)
    request = sync_request()
    first = uuid4()
    gateway.capture(request, first)
    store.apply(gateway, request, first)
    native.account.balance = 11000.0
    second = uuid4()
    gateway.capture(request, second)
    assert store.apply(gateway, request, second).code == "STALE_OBSERVATION"
    assert str(store.view(request.connection).batch.account.balance) == "10000.0"


def test_projection_audit_write_failure_is_atomic_and_can_retry_same_receipt(tmp_path, monkeypatch):
    module = implementation()
    gateway, calls = fixture_gateway(tmp_path / "gateway.sqlite", SyncNative(), [NOW])
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: NOW)
    request = sync_request()
    attempt = uuid4()
    gateway.capture(request, attempt)
    append = module.AuditChain.append

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("fixture write failure")

    monkeypatch.setattr(module.AuditChain, "append", fail)
    assert store.apply(gateway, request, attempt).code == "STORAGE_UNAVAILABLE"
    assert store.view(request.connection) is None
    monkeypatch.setattr(module.AuditChain, "append", append)
    assert store.apply(gateway, request, attempt).state == "APPLIED" and len(calls) == 1


def test_unavailable_projection_reads_have_fixed_error(tmp_path):
    import pytest

    module = implementation()
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: NOW)
    store.close()
    with pytest.raises(module.ProjectionRejected, match="^STORAGE_UNAVAILABLE$"):
        store.view(sync_request().connection)


def test_exported_projection_contract_rejects_order_authority(tmp_path):
    import json
    from pathlib import Path

    import pytest
    from jsonschema import Draft202012Validator, ValidationError
    from pydantic import TypeAdapter

    module = implementation()
    schema_path = (
        Path(__file__).resolve().parents[1] / "docs/schemas/demo-sync-projection.schema.json"
    )
    assert schema_path.is_file()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert (
        schema
        == TypeAdapter(
            module.ProjectionResult | module.ProjectionView | module.DealVersion
        ).json_schema()
    )
    result = module.ProjectionResult(state="DISABLED", attempt_id=uuid4()).model_dump(mode="json")
    validator = Draft202012Validator(schema)
    validator.validate(result)
    result["may_submit_orders"] = True
    with pytest.raises(ValidationError):
        validator.validate(result)


def test_foreign_connection_cannot_read_retained_account(tmp_path):
    import pytest

    module = implementation()
    gateway, _ = fixture_gateway(tmp_path / "gateway.sqlite", SyncNative(), [NOW])
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: NOW)
    request = sync_request()
    attempt = uuid4()
    gateway.capture(request, attempt)
    store.apply(gateway, request, attempt)
    other = request.connection.model_copy(update={"enrollment_id": uuid4()})
    with pytest.raises(module.ProjectionRejected, match="^FOREIGN_SCOPE$"):
        store.view(other)


def test_receipt_capacity_withholds_whole_import(tmp_path, monkeypatch):
    module = implementation()
    time = [NOW]
    gateway, _ = fixture_gateway(tmp_path / "gateway.sqlite", SyncNative(), time)
    store = module.SyncProjectionStore(tmp_path / "projection.sqlite", clock=lambda: time[0])
    request = sync_request()
    first = uuid4()
    gateway.capture(request, first)
    store.apply(gateway, request, first)
    monkeypatch.setattr(module, "MAX_RECEIPTS", 1)
    time[0] += timedelta(seconds=1)
    second = uuid4()
    gateway.capture(request, second)
    assert store.apply(gateway, request, second).code == "CAPACITY"
    assert store.view(request.connection).receipts == 1

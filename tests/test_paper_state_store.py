import sqlite3

import pytest

from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.risk import SafetyState


def test_failed_recovery_rolls_back_state_receipt_and_audit(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    store = PaperStateStore(path)
    store.record_order_uncertainty("order-1")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TRIGGER reject_recovery_audit BEFORE INSERT ON audit_events
            WHEN NEW.event_key LIKE 'recovery:%'
            BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        store.record_recovery("recover", "checked", {"receipt_id": "receipt-1"})
    assert store.safety_state() == SafetyState.NO_NEW_RISK
    assert store.recovery_receipt("recover") is None
    assert len(store.audit_events()) == 1
    assert PaperStateStore(path).safety_state() == SafetyState.NO_NEW_RISK


def test_legacy_safety_history_is_migrated_without_lowering_halt(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE control_receipts (
                idempotency_key TEXT PRIMARY KEY, safety_state TEXT NOT NULL,
                reason TEXT NOT NULL, receipt_json TEXT NOT NULL)"""
        )
        connection.execute("INSERT INTO control_receipts VALUES ('halt', 'HALT', 'incident', '{}')")
    store = PaperStateStore(path)
    assert store.safety_state() == SafetyState.HALT
    with pytest.raises(ValueError, match="Only NO_NEW_RISK"):
        store.record_recovery("recover", "checked", {"receipt_id": "receipt-1"})
    assert store.recovery_receipt("recover") is None


def test_repeated_uncertainty_after_recovery_reescalates_state(tmp_path) -> None:
    store = PaperStateStore(tmp_path / "paper.sqlite3")
    store.record_order_uncertainty("position:EURUSD")
    store.record_recovery("recover", "checked", {"receipt_id": "receipt-1"})
    assert store.safety_state() == SafetyState.NORMAL
    store.record_order_uncertainty("position:EURUSD")
    assert store.safety_state() == SafetyState.NO_NEW_RISK
    assert [event["action"] for event in store.audit_events()] == [
        "NO_NEW_RISK",
        "PAPER_RECOVERY",
        "NO_NEW_RISK",
    ]

import sqlite3
from decimal import Decimal

from ai_quant_os.order_store import PaperOrderStore


def test_existing_paper_order_database_gains_unique_intent_index(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    legacy_order = (
        '{"intent_id": "legacy-intent", "order_id": "legacy-order", '
        '"command": {"instrument_id": "EURUSD", "side": "BUY", "quantity": "1"}}'
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE paper_orders (
                idempotency_key TEXT PRIMARY KEY,
                intent_json TEXT NOT NULL,
                order_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO paper_orders VALUES (?, ?, ?)",
            ("legacy-key", "{}", legacy_order),
        )

    store = PaperOrderStore(path)
    assert store.get_by_intent_id("legacy-intent") == legacy_order
    assert store.get_order_by_id("legacy-order") is not None
    assert not store.insert(
        "new-key",
        "legacy-intent",
        "{}",
        '{"intent_id": "legacy-intent", "order_id": "new-order", '
        '"command": {"instrument_id": "EURUSD", "side": "BUY", "quantity": "1"}}',
        "new-order",
        lambda: None,
        expected_snapshot_id=store.exposure_snapshot().snapshot_id,
        max_portfolio_exposure=Decimal("1500"),
    )


def test_interrupted_column_backfill_is_completed_on_startup(tmp_path) -> None:
    path = tmp_path / "partial.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE paper_orders (
                idempotency_key TEXT PRIMARY KEY,
                intent_json TEXT NOT NULL,
                order_json TEXT NOT NULL,
                intent_id TEXT,
                order_id TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO paper_orders VALUES (?, ?, ?, NULL, NULL)",
            ("legacy-key", "{}", '{"intent_id": "legacy-intent", "order_id": "legacy-order"}'),
        )
    store = PaperOrderStore(path)
    assert store.get_by_intent_id("legacy-intent") is not None
    assert store.get_order_by_id("legacy-order") is not None

import sqlite3

from ai_quant_os.order_store import PaperOrderStore


def test_existing_paper_order_database_gains_unique_intent_index(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
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
            ("legacy-key", "{}", '{"intent_id": "legacy-intent"}'),
        )

    store = PaperOrderStore(path)
    assert store.get_by_intent_id("legacy-intent") == '{"intent_id": "legacy-intent"}'
    assert not store.insert(
        "new-key", "legacy-intent", "{}", '{"intent_id": "legacy-intent"}', lambda: None
    )

import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Callable


class PaperOrderStore:
    def __init__(self, path: Path | None = None) -> None:
        self._connection = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False
        )
        self._lock = Lock()
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS paper_orders (
                idempotency_key TEXT PRIMARY KEY,
                intent_json TEXT NOT NULL,
                order_json TEXT NOT NULL,
                intent_id TEXT UNIQUE
            )"""
        )
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(paper_orders)")}
        if "intent_id" not in columns:
            with self._connection:
                self._connection.execute("ALTER TABLE paper_orders ADD COLUMN intent_id TEXT")
                rows = self._connection.execute(
                    "SELECT idempotency_key, order_json FROM paper_orders"
                ).fetchall()
                for key, raw in rows:
                    self._connection.execute(
                        "UPDATE paper_orders SET intent_id = ? WHERE idempotency_key = ?",
                        (json.loads(raw)["intent_id"], key),
                    )
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_intent_id ON paper_orders(intent_id)"
        )
        self._connection.commit()

    def get(self, key: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT intent_json, order_json FROM paper_orders WHERE idempotency_key = ?", (key,)
            ).fetchone()
            return (row[0], row[1]) if row else None

    def all_orders(self) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT order_json FROM paper_orders ORDER BY rowid"
            ).fetchall()
            return tuple(row[0] for row in rows)

    def get_by_intent_id(self, intent_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT order_json FROM paper_orders WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return row[0] if row else None

    def insert(
        self,
        key: str,
        intent_id: str,
        intent_json: str,
        order_json: str,
        before_commit: Callable[[], None],
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO paper_orders (idempotency_key, intent_id, intent_json, order_json) VALUES (?, ?, ?, ?)",
                (key, intent_id, intent_json, order_json),
            )
            if cursor.rowcount == 0:
                return False
            before_commit()
            return True

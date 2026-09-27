import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Callable

from ai_quant_os.portfolio import PaperFill


def _encode_fill(fill: PaperFill) -> str:
    return json.dumps(
        asdict(fill),
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
        sort_keys=True,
    )


def _decode_fill(raw: str) -> PaperFill:
    data = json.loads(raw)
    for field in ("quantity", "price", "fee"):
        data[field] = Decimal(data[field])
    data["observed_at"] = datetime.fromisoformat(data["observed_at"])
    return PaperFill(**data)


class PaperOrderStore:
    def __init__(self, path: Path | None = None) -> None:
        self._connection = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False, timeout=30
        )
        self._lock = Lock()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS paper_orders (
                    idempotency_key TEXT PRIMARY KEY,
                    intent_json TEXT NOT NULL,
                    order_json TEXT NOT NULL,
                    intent_id TEXT UNIQUE,
                    order_id TEXT UNIQUE
                )"""
            )
            columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(paper_orders)")
            }
            for column in ("intent_id", "order_id"):
                if column not in columns:
                    self._connection.execute(f"ALTER TABLE paper_orders ADD COLUMN {column} TEXT")
            rows = self._connection.execute(
                "SELECT idempotency_key, order_json, intent_id, order_id FROM paper_orders"
            ).fetchall()
            for key, raw, intent_id, order_id in rows:
                order = json.loads(raw)
                if (intent_id is not None and intent_id != order["intent_id"]) or (
                    order_id is not None and order_id != order["order_id"]
                ):
                    raise ValueError("Paper order index disagrees with receipt")
                if intent_id is None or order_id is None:
                    self._connection.execute(
                        "UPDATE paper_orders SET intent_id = ?, order_id = ? WHERE idempotency_key = ?",
                        (order["intent_id"], order["order_id"], key),
                    )
            self._connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_intent_id ON paper_orders(intent_id)"
            )
            self._connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_order_id ON paper_orders(order_id)"
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS paper_fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    fill_json TEXT NOT NULL
                )"""
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_fills_order_id ON paper_fills(order_id)"
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

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

    def get_order_by_id(self, order_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT order_json FROM paper_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            return row[0] if row else None

    def all_fills(self) -> tuple[PaperFill, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT fill_json FROM paper_fills ORDER BY rowid"
            ).fetchall()
            return tuple(_decode_fill(row[0]) for row in rows)

    def record_fill(self, fill: PaperFill) -> PaperFill:
        encoded = _encode_fill(fill)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                prior = self._connection.execute(
                    "SELECT fill_json FROM paper_fills WHERE fill_id = ?", (fill.fill_id,)
                ).fetchone()
                if prior is not None:
                    if prior[0] != encoded:
                        raise ValueError("fill id reused with different details")
                    self._connection.commit()
                    return _decode_fill(prior[0])
                order = self._connection.execute(
                    "SELECT order_json, intent_json FROM paper_orders WHERE order_id = ?",
                    (fill.order_id,),
                ).fetchone()
                if order is None:
                    raise ValueError("Paper order does not exist")
                order_data = json.loads(order[0])
                command = order_data["command"]
                intent_data = json.loads(order[1])
                if order_data["status"] != "ACCEPTED":
                    raise ValueError("Paper order is not accepted")
                if fill.instrument_id != command["instrument_id"] or fill.side != command["side"]:
                    raise ValueError("Fill does not match paper order")
                if fill.observed_at < datetime.fromisoformat(intent_data["created_at"]):
                    raise ValueError("Fill occurred before intent creation")
                if fill.observed_at > datetime.fromisoformat(command["expires_at"]):
                    raise ValueError("Fill occurred after order expiry")
                rows = self._connection.execute(
                    "SELECT fill_json FROM paper_fills WHERE order_id = ?", (fill.order_id,)
                ).fetchall()
                filled_quantity = sum((_decode_fill(row[0]).quantity for row in rows), Decimal(0))
                if filled_quantity + fill.quantity > Decimal(command["quantity"]):
                    raise ValueError("Fill exceeds order quantity")
                self._connection.execute(
                    "INSERT INTO paper_fills VALUES (?, ?, ?)",
                    (fill.fill_id, fill.order_id, encoded),
                )
                self._connection.commit()
                return fill
            except BaseException:
                self._connection.rollback()
                raise

    def insert(
        self,
        key: str,
        intent_id: str,
        intent_json: str,
        order_json: str,
        order_id: str,
        before_commit: Callable[[], None],
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO paper_orders (idempotency_key, intent_id, order_id, intent_json, order_json) VALUES (?, ?, ?, ?, ?)",
                (key, intent_id, order_id, intent_json, order_json),
            )
            if cursor.rowcount == 0:
                return False
            before_commit()
            return True

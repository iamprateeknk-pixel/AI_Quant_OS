import json
import sqlite3
from pathlib import Path
from threading import Lock

from ai_quant_os.risk import SafetyState


class PaperStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self._connection = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False
        )
        self._lock = Lock()
        with self._connection:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS control_receipts (
                    idempotency_key TEXT PRIMARY KEY,
                    safety_state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    receipt_json TEXT NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS audit_events (
                    event_key TEXT PRIMARY KEY,
                    event_json TEXT NOT NULL
                )"""
            )

    def safety_state(self) -> SafetyState:
        with self._lock:
            row = self._connection.execute(
                "SELECT safety_state FROM control_receipts ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            return SafetyState(row[0]) if row else SafetyState.NORMAL

    def control_receipt(self, key: str) -> tuple[SafetyState, str, dict[str, str]] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT safety_state, reason, receipt_json FROM control_receipts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            return (SafetyState(row[0]), row[1], json.loads(row[2])) if row else None

    def record_control(
        self, key: str, state: SafetyState, reason: str, receipt: dict[str, str]
    ) -> None:
        event = {
            "action": state.value,
            "intent_id": "",
            "reason": reason,
            "trace_id": receipt["receipt_id"],
        }
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO control_receipts VALUES (?, ?, ?, ?)",
                (key, state.value, reason, json.dumps(receipt, sort_keys=True)),
            )
            self._connection.execute(
                "INSERT INTO audit_events VALUES (?, ?)",
                (f"control:{key}", json.dumps(event, sort_keys=True)),
            )

    def record_event(self, key: str, event: dict[str, str]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO audit_events VALUES (?, ?)",
                (key, json.dumps(event, sort_keys=True)),
            )

    def audit_events(self) -> list[dict[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json FROM audit_events ORDER BY rowid"
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

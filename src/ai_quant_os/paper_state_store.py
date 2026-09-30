import json
import sqlite3
from pathlib import Path
from threading import Lock
from uuid import uuid4

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
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS order_safety_escalations (
                    order_id TEXT PRIMARY KEY,
                    safety_state TEXT NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS safety_current (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    safety_state TEXT NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS recovery_receipts (
                    idempotency_key TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    receipt_json TEXT NOT NULL
                )"""
            )
            legacy_states = self._connection.execute(
                "SELECT safety_state FROM control_receipts UNION ALL "
                "SELECT safety_state FROM order_safety_escalations"
            ).fetchall()
            severity = list(SafetyState)
            legacy_state = max(
                (SafetyState(row[0]) for row in legacy_states),
                key=severity.index,
                default=SafetyState.NORMAL,
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO safety_current VALUES (1, ?)", (legacy_state.value,)
            )

    def safety_state(self) -> SafetyState:
        with self._lock:
            row = self._connection.execute(
                "SELECT safety_state FROM safety_current WHERE singleton = 1"
            ).fetchone()
            return SafetyState(row[0])

    def _escalate(self, state: SafetyState) -> bool:
        row = self._connection.execute(
            "SELECT safety_state FROM safety_current WHERE singleton = 1"
        ).fetchone()
        severity = list(SafetyState)
        if severity.index(state) > severity.index(SafetyState(row[0])):
            self._connection.execute(
                "UPDATE safety_current SET safety_state = ? WHERE singleton = 1",
                (state.value,),
            )
            return True
        return False

    def record_order_uncertainty(self, order_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            escalated = self._escalate(SafetyState.NO_NEW_RISK)
            self._connection.execute(
                "INSERT OR IGNORE INTO order_safety_escalations VALUES (?, ?)",
                (order_id, SafetyState.NO_NEW_RISK.value),
            )
            event_key = f"order-uncertain:{order_id}"
            if (
                escalated
                and self._connection.execute(
                    "SELECT 1 FROM audit_events WHERE event_key = ?", (event_key,)
                ).fetchone()
            ):
                event_key = f"{event_key}:{uuid4()}"
            self._connection.execute(
                "INSERT OR IGNORE INTO audit_events VALUES (?, ?)",
                (
                    event_key,
                    json.dumps(
                        {
                            "action": SafetyState.NO_NEW_RISK.value,
                            "intent_id": "",
                            "reason": "PAPER_RECONCILIATION_REQUIRED",
                            "trace_id": order_id,
                        },
                        sort_keys=True,
                    ),
                ),
            )

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
            self._connection.execute("BEGIN IMMEDIATE")
            self._escalate(state)
            self._connection.execute(
                "INSERT INTO control_receipts VALUES (?, ?, ?, ?)",
                (key, state.value, reason, json.dumps(receipt, sort_keys=True)),
            )
            self._connection.execute(
                "INSERT INTO audit_events VALUES (?, ?)",
                (f"control:{key}", json.dumps(event, sort_keys=True)),
            )

    def recovery_receipt(self, key: str) -> tuple[str, dict[str, str]] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT reason, receipt_json FROM recovery_receipts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            return (row[0], json.loads(row[1])) if row else None

    def record_recovery(self, key: str, reason: str, receipt: dict[str, str]) -> None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT safety_state FROM safety_current WHERE singleton = 1"
            ).fetchone()
            if row[0] != SafetyState.NO_NEW_RISK.value:
                raise ValueError("Only NO_NEW_RISK paper state can be recovered")
            self._connection.execute(
                "INSERT INTO recovery_receipts VALUES (?, ?, ?)",
                (key, reason, json.dumps(receipt, sort_keys=True)),
            )
            self._connection.execute(
                "INSERT INTO audit_events VALUES (?, ?)",
                (
                    f"recovery:{key}",
                    json.dumps(
                        {
                            "action": "PAPER_RECOVERY",
                            "intent_id": "",
                            "reason": reason,
                            "trace_id": receipt["receipt_id"],
                        },
                        sort_keys=True,
                    ),
                ),
            )
            self._connection.execute(
                "UPDATE safety_current SET safety_state = ? WHERE singleton = 1",
                (SafetyState.NORMAL.value,),
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

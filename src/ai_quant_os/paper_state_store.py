import json
from hashlib import sha256
import sqlite3
from pathlib import Path
from threading import Lock
from uuid import uuid4

from ai_quant_os.risk import SafetyState
from ai_quant_os.audit_chain import AuditChain, AuditIntegrity
from ai_quant_os.agent_runs import AgentRun
from ai_quant_os.agent_run_repository import AgentRunRepository
from ai_quant_os.agent_trace_binding import AgentTraceBinding
from ai_quant_os.agent_trace_binding_repository import AgentTraceBindingRepository
from ai_quant_os.context_checkpoints import ContextCheckpoint
from ai_quant_os.context_checkpoint_repository import ContextCheckpointRepository


class OperatorReservationConflict(ValueError):
    """A different command attempted to reuse original operator custody."""


class PaperStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self._connection = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False
        )
        self._lock = Lock()
        self._audit_chain = AuditChain(self._connection)
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
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
            self._connection.execute("CREATE TABLE IF NOT EXISTS operator_reservations (action_key TEXT PRIMARY KEY, pending_json TEXT NOT NULL)")
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

            self._audit_chain.initialize()
            self._agent_runs = AgentRunRepository(self._connection)
            self._agent_trace_bindings = AgentTraceBindingRepository(self._connection)
            self._context_checkpoints = ContextCheckpointRepository(self._connection)

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
            self._audit_chain.append(
                event_key,
                {
                    "action": SafetyState.NO_NEW_RISK.value,
                    "intent_id": "",
                    "reason": "PAPER_RECONCILIATION_REQUIRED",
                    "trace_id": order_id,
                },
            )

    def control_receipt(self, key: str) -> tuple[SafetyState, str, dict[str, str]] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT safety_state, reason, receipt_json FROM control_receipts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            return (SafetyState(row[0]), row[1], json.loads(row[2])) if row else None

    def operator_control_receipt(self, key: str) -> dict | None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            self._audit_chain.require_valid()
            row = self._connection.execute(
                "SELECT receipt_json FROM control_receipts WHERE idempotency_key=?", (key,)
            ).fetchone()
            if row:
                self._audit_chain.require_valid()
                event = self._connection.execute(
                    "SELECT event_json FROM audit_events WHERE event_key=?", (f"control:{key}",)
                ).fetchone()
                if event is None or json.loads(event[0]).get("receipt_sha256") != sha256(row[0].encode()).hexdigest():
                    raise ValueError("Operator receipt audit binding mismatch")
                return json.loads(row[0])
            return None

    def record_control(
        self, key: str, state: SafetyState, reason: str, receipt: dict[str, str],
        *, expected_state: SafetyState | None = None,
    ) -> None:
        event = {
            "action": state.value,
            "intent_id": "",
            "reason": reason,
            "trace_id": receipt["receipt_id"],
        }
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            if expected_state is not None:
                self._check_operator_reservation(key, receipt)
                current = SafetyState(self._connection.execute(
                    "SELECT safety_state FROM safety_current WHERE singleton=1"
                ).fetchone()[0])
                if current != expected_state:
                    raise ValueError("Expected safety state does not match current state")
                if list(SafetyState).index(state) < list(SafetyState).index(current):
                    raise ValueError("Safety state recovery requires a separate gated workflow")
                event["receipt_sha256"] = sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
            self._escalate(state)
            self._connection.execute(
                "INSERT INTO control_receipts VALUES (?, ?, ?, ?)",
                (key, state.value, reason, json.dumps(receipt, sort_keys=True)),
            )
            self._audit_chain.append(f"control:{key}", event)

    def _operator_pending(self, key: str) -> dict | None:
        row = self._connection.execute("SELECT pending_json FROM operator_reservations WHERE action_key=?", (key,)).fetchone()
        event = self._connection.execute("SELECT event_json FROM audit_events WHERE event_key=?", ("operator-reserved:" + key,)).fetchone()
        if row is None and event is None:
            return None
        if row is None or event is None or len(row[0]) > 2048 or json.loads(event[0]).get("receipt_sha256") != sha256(row[0].encode()).hexdigest():
            raise ValueError("Operator reservation audit binding mismatch")
        return json.loads(row[0])

    def _check_operator_reservation(self, key: str, receipt: dict) -> None:
        pending = self._operator_pending(key)
        if pending is not None and any(pending[field] != receipt[field] for field in (
            "receipt_id", "action_id", "operator_session_id", "request_sha256", "trace_id"
        )):
            raise ValueError("Terminal receipt does not match original reservation")

    def operator_pending(self, key: str) -> dict | None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            self._audit_chain.require_valid()
            return self._operator_pending(key)

    def reserve_operator_control(self, key: str, reason: str, receipt: dict) -> dict | None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._audit_chain.require_valid()
            if self._connection.execute("SELECT 1 FROM control_receipts WHERE idempotency_key=?", (key,)).fetchone():
                return None
            prior = self._operator_pending(key)
            if prior is not None:
                if prior["request_sha256"] != receipt["request_sha256"]:
                    raise OperatorReservationConflict("Action identity reused with a different command")
                return prior
            if self._connection.execute("SELECT COUNT(*) FROM operator_reservations").fetchone()[0] >= 4096:
                raise ValueError("Operator reservation capacity exceeded")
            current = self._connection.execute("SELECT safety_state FROM safety_current WHERE singleton=1").fetchone()[0]
            result = {**receipt, "previous_state": current, "authoritative_state": current}
            raw = json.dumps(result, sort_keys=True)
            if len(raw.encode()) > 2048 or result["decision"] != "PENDING":
                raise ValueError("Bounded pending receipt required")
            self._connection.execute("INSERT INTO operator_reservations VALUES (?,?)", (key, raw))
            self._audit_chain.append("operator-reserved:" + key, {
                "action": "OPERATOR_CONTROL_RESERVED", "intent_id": "", "reason": reason,
                "trace_id": result["receipt_id"], "receipt_sha256": sha256(raw.encode()).hexdigest(),
            })
            return result

    def record_operator_rejection(self, key: str, reason: str, receipt: dict) -> None:
        if receipt.get("decision") != "REJECTED":
            raise ValueError("Terminal rejection receipt required")
        raw = json.dumps(receipt, sort_keys=True)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._audit_chain.require_valid()
            self._check_operator_reservation(key, receipt)
            current = self._connection.execute(
                "SELECT safety_state FROM safety_current WHERE singleton=1"
            ).fetchone()[0]
            if receipt["previous_state"] != current or receipt["authoritative_state"] != current:
                raise ValueError("Safety state changed before rejection publication")
            self._connection.execute("INSERT INTO control_receipts VALUES (?,?,?,?)", (key, current, reason, raw))
            self._audit_chain.append(f"control:{key}", {
                "action": "OPERATOR_CONTROL_REJECTED", "intent_id": "", "reason": reason,
                "trace_id": receipt["receipt_id"], "receipt_sha256": sha256(raw.encode()).hexdigest(),
            })

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
            self._audit_chain.append(
                f"recovery:{key}",
                {
                    "action": "PAPER_RECOVERY",
                    "intent_id": "",
                    "reason": reason,
                    "trace_id": receipt["receipt_id"],
                },
            )
            self._connection.execute(
                "UPDATE safety_current SET safety_state = ? WHERE singleton = 1",
                (SafetyState.NORMAL.value,),
            )

    def record_event(self, key: str, event: dict[str, str]) -> None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._audit_chain.append(key, event)

    def record_agent_run(
        self, run: AgentRun, *, trace_binding: AgentTraceBinding | None = None
    ) -> bool:
        run = AgentRun.model_validate_json(run.model_dump_json())
        if trace_binding is not None:
            trace_binding = AgentTraceBinding.model_validate_json(trace_binding.model_dump_json())
            trace_binding.require_run(run)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._audit_chain.require_valid()
            self._agent_trace_bindings.get(run.run_id, self._agent_runs.get(run.run_id))
            inserted = self._agent_runs.put(run)
            self._audit_chain.append(f"classification:{run.run_id}", run.audit_event())
            if trace_binding is not None:
                self._agent_trace_bindings.put(trace_binding, run, inserted_run=inserted)
                self._audit_chain.append(f"agent-trace:{run.run_id}", trace_binding.audit_event())
            return inserted

    def agent_run(self, run_id: str) -> AgentRun | None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            self._audit_chain.require_valid()
            run = self._agent_runs.get(run_id)
            self._agent_trace_bindings.get(run_id, run)
            return run


    def agent_trace_binding(self, run_id: str) -> AgentTraceBinding | None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            self._audit_chain.require_valid()
            return self._agent_trace_bindings.get(run_id, self._agent_runs.get(run_id))

    def record_context_checkpoint(self, checkpoint: ContextCheckpoint) -> bool:
        checkpoint = ContextCheckpoint.model_validate_json(checkpoint.model_dump_json())
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._audit_chain.require_valid()
            inserted = self._context_checkpoints.put(checkpoint)
            self._audit_chain.append(
                f"agent-context:{checkpoint.context.context_ref}", checkpoint.audit_event()
            )
            return inserted

    def context_checkpoint(self, context_ref: str) -> ContextCheckpoint | None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            self._audit_chain.require_valid()
            return self._context_checkpoints.get(context_ref)

    def backfill_event(self, key: str, event: dict[str, str]) -> None:
        """Reconstruct missing receipt events at startup; retain original history if present."""
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._audit_chain.append(key, event, only_if_missing=True)

    def audit_integrity(self) -> AuditIntegrity:
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            return self._audit_chain.verify()

    def audit_event(self, key: str) -> dict[str, str] | None:
        """Read one receipt only after validating its complete owning audit chain."""
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            self._audit_chain.require_valid()
            row = self._connection.execute(
                "SELECT event_json FROM audit_events WHERE event_key = ?", (key,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def audit_events(self) -> list[dict[str, str]]:
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            self._audit_chain.require_valid()
            rows = self._connection.execute(
                "SELECT e.event_json FROM audit_chain c "
                "JOIN audit_events e ON e.event_key = c.event_key ORDER BY c.sequence"
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

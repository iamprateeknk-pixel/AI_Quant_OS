import json
from datetime import datetime
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
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS operator_reservations (action_key TEXT PRIMARY KEY, pending_json TEXT NOT NULL)"
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS broker_route_state (
                    broker_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    latest_action_id TEXT,
                    state_sha256 TEXT NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS broker_route_control_receipts (
                    action_id TEXT PRIMARY KEY,
                    receipt_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL
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

    @staticmethod
    def _broker_route_state_hash(broker_id: str, enabled: bool, source: str) -> str:
        raw = json.dumps(
            {"broker_id": broker_id, "enabled": enabled, "source": source},
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(raw.encode()).hexdigest()

    def _broker_route_receipt_locked(self, action_id: str) -> dict | None:
        row = self._connection.execute(
            "SELECT receipt_json, reason, request_sha256 FROM broker_route_control_receipts "
            "WHERE action_id=?",
            (action_id,),
        ).fetchone()
        event = self._connection.execute(
            "SELECT event_json FROM audit_events WHERE event_key=?",
            ("broker-route-control:" + action_id,),
        ).fetchone()
        if row is None and event is None:
            return None
        if row is None or event is None:
            raise ValueError("Broker route receipt audit binding is missing")
        receipt_raw, reason, fingerprint = row
        receipt = json.loads(receipt_raw)
        audit = json.loads(event[0])
        if (
            len(receipt_raw.encode()) > 4096
            or receipt.get("action_id") != action_id
            or receipt.get("request_sha256") != fingerprint
            or audit.get("receipt_sha256") != sha256(receipt_raw.encode()).hexdigest()
            or audit.get("reason") != reason
            or audit.get("trace_id") != receipt.get("trace_id")
        ):
            raise ValueError("Broker route receipt audit binding mismatch")
        return receipt

    def _verified_broker_route_state_locked(self, broker_id: str) -> bool | None:
        row = self._connection.execute(
            "SELECT enabled, latest_action_id, state_sha256 FROM broker_route_state "
            "WHERE broker_id=?",
            (broker_id,),
        ).fetchone()
        if row is None:
            return None
        stored_enabled, action_id, digest = row
        enabled = stored_enabled == 1
        if action_id is None:
            expected = self._broker_route_state_hash(broker_id, enabled, "DEFAULT")
            if digest != expected:
                raise ValueError("Broker route default state could not be verified")
            return enabled
        receipt = self._broker_route_receipt_locked(action_id)
        receipt_row = self._connection.execute(
            "SELECT receipt_json FROM broker_route_control_receipts WHERE action_id=?",
            (action_id,),
        ).fetchone()
        if (
            receipt is None
            or receipt_row is None
            or digest != sha256(receipt_row[0].encode()).hexdigest()
            or receipt.get("broker_id") != broker_id
            or receipt.get("authoritative_enabled") is not enabled
        ):
            raise ValueError("Broker route state receipt binding mismatch")
        return enabled

    def broker_route_enabled(self, broker_id: str, *, default_enabled: bool) -> bool:
        if not broker_id or type(default_enabled) is not bool:
            raise ValueError("Broker route identity and boolean default are required")
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._audit_chain.require_valid()
            current = self._verified_broker_route_state_locked(broker_id)
            if current is not None:
                return current
            digest = self._broker_route_state_hash(broker_id, default_enabled, "DEFAULT")
            self._connection.execute(
                "INSERT INTO broker_route_state VALUES (?, ?, NULL, ?)",
                (broker_id, int(default_enabled), digest),
            )
            return default_enabled

    def record_broker_route_control(
        self,
        key: str,
        reason: str,
        receipt: dict,
        *,
        current_enabled: bool,
        rejection_reason: str | None = None,
    ) -> dict:
        action_id = receipt.get("action_id")
        expected_enabled = receipt.get("expected_enabled")
        requested_enabled = receipt.get("requested_enabled")
        fingerprint = receipt.get("request_sha256")
        if (
            not isinstance(action_id, str)
            or key != "broker-route:" + action_id
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 512
            or type(current_enabled) is not bool
            or type(expected_enabled) is not bool
            or type(requested_enabled) is not bool
            or not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
            or rejection_reason
            not in {
                None,
                "STALE_COMMAND",
                "BROKER_DISCONNECTED",
                "BROKER_NOT_ELIGIBLE",
                "SAFETY_STATE_BLOCKS_ENABLE",
            }
        ):
            raise ValueError("Broker route control receipt is invalid")
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._audit_chain.require_valid()
            previous_receipt = self._broker_route_receipt_locked(action_id)
            if previous_receipt is not None:
                if previous_receipt["request_sha256"] != fingerprint:
                    raise OperatorReservationConflict(
                        "Broker route action identity was reused for a different command"
                    )
                return previous_receipt
            persisted_enabled = self._verified_broker_route_state_locked(
                receipt.get("broker_id", "")
            )
            if persisted_enabled is None or persisted_enabled is not current_enabled:
                raise ValueError("Broker route runtime and durable state differ")
            accepted = expected_enabled is current_enabled and rejection_reason is None
            authoritative_enabled = requested_enabled if accepted else current_enabled
            denied_reason = None if accepted else rejection_reason or "STALE_STATE"
            result = {
                **receipt,
                "decision": "ACCEPTED" if accepted else "REJECTED",
                "previous_enabled": current_enabled,
                "authoritative_enabled": authoritative_enabled,
                "rejection_reason": denied_reason,
                "policy_checks": [
                    "PAPER_ONLY",
                    "VALID_SESSION",
                    "FRESH_REAUTH",
                    (
                        "EXPECTED_STATE"
                        if accepted
                        else "COMMAND_FRESHNESS_REJECTED"
                        if rejection_reason == "STALE_COMMAND"
                        else "EXPECTED_STATE_REJECTED"
                    ),
                    "NEW_ORDERS_ONLY",
                ],
            }
            raw = json.dumps(result, sort_keys=True, separators=(",", ":"))
            if len(raw.encode()) > 4096:
                raise ValueError("Broker route receipt exceeds its limit")
            digest = sha256(raw.encode()).hexdigest()
            event_key = "broker-route-control:" + action_id
            self._connection.execute(
                "INSERT INTO broker_route_control_receipts VALUES (?, ?, ?, ?)",
                (action_id, raw, reason, fingerprint),
            )
            self._connection.execute(
                "UPDATE broker_route_state SET enabled=?, latest_action_id=?, state_sha256=? "
                "WHERE broker_id=?",
                (int(authoritative_enabled), action_id, digest, receipt["broker_id"]),
            )
            self._audit_chain.append(
                event_key,
                {
                    "action": (
                        "BROKER_ROUTE_ENABLED"
                        if accepted and requested_enabled
                        else "BROKER_ROUTE_DISABLED"
                        if accepted
                        else "BROKER_ROUTE_CONTROL_REJECTED"
                    ),
                    "intent_id": "",
                    "reason": reason,
                    "receipt_sha256": digest,
                    "trace_id": receipt["trace_id"],
                },
            )
            return result

    def broker_route_control_receipt(self, action_id: str) -> dict | None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            self._audit_chain.require_valid()
            return self._broker_route_receipt_locked(action_id)

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
                if (
                    event is None
                    or json.loads(event[0]).get("receipt_sha256")
                    != sha256(row[0].encode()).hexdigest()
                ):
                    raise ValueError("Operator receipt audit binding mismatch")
                return json.loads(row[0])
            return None

    def record_control(
        self,
        key: str,
        state: SafetyState,
        reason: str,
        receipt: dict[str, str],
        *,
        expected_state: SafetyState | None = None,
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
                current = SafetyState(
                    self._connection.execute(
                        "SELECT safety_state FROM safety_current WHERE singleton=1"
                    ).fetchone()[0]
                )
                if current != expected_state:
                    raise ValueError("Expected safety state does not match current state")
                if list(SafetyState).index(state) < list(SafetyState).index(current):
                    raise ValueError("Safety state recovery requires a separate gated workflow")
                event["receipt_sha256"] = sha256(
                    json.dumps(receipt, sort_keys=True).encode()
                ).hexdigest()
            self._escalate(state)
            self._connection.execute(
                "INSERT INTO control_receipts VALUES (?, ?, ?, ?)",
                (key, state.value, reason, json.dumps(receipt, sort_keys=True)),
            )
            self._audit_chain.append(f"control:{key}", event)

    def _operator_pending(self, key: str) -> dict | None:
        row = self._connection.execute(
            "SELECT pending_json FROM operator_reservations WHERE action_key=?", (key,)
        ).fetchone()
        event = self._connection.execute(
            "SELECT event_json FROM audit_events WHERE event_key=?", ("operator-reserved:" + key,)
        ).fetchone()
        if row is None and event is None:
            return None
        if (
            row is None
            or event is None
            or len(row[0]) > 2048
            or json.loads(event[0]).get("receipt_sha256") != sha256(row[0].encode()).hexdigest()
        ):
            raise ValueError("Operator reservation audit binding mismatch")
        return json.loads(row[0])

    def _check_operator_reservation(self, key: str, receipt: dict) -> None:
        pending = self._operator_pending(key)
        if pending is not None and any(
            pending[field] != receipt[field]
            for field in (
                "receipt_id",
                "action_id",
                "operator_session_id",
                "request_sha256",
                "trace_id",
            )
        ):
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
            if self._connection.execute(
                "SELECT 1 FROM control_receipts WHERE idempotency_key=?", (key,)
            ).fetchone():
                return None
            prior = self._operator_pending(key)
            if prior is not None:
                if prior["request_sha256"] != receipt["request_sha256"]:
                    raise OperatorReservationConflict(
                        "Action identity reused with a different command"
                    )
                return prior
            if (
                self._connection.execute("SELECT COUNT(*) FROM operator_reservations").fetchone()[0]
                >= 4096
            ):
                raise ValueError("Operator reservation capacity exceeded")
            current = self._connection.execute(
                "SELECT safety_state FROM safety_current WHERE singleton=1"
            ).fetchone()[0]
            result = {**receipt, "previous_state": current, "authoritative_state": current}
            raw = json.dumps(result, sort_keys=True)
            if len(raw.encode()) > 2048 or result["decision"] != "PENDING":
                raise ValueError("Bounded pending receipt required")
            self._connection.execute("INSERT INTO operator_reservations VALUES (?,?)", (key, raw))
            self._audit_chain.append(
                "operator-reserved:" + key,
                {
                    "action": "OPERATOR_CONTROL_RESERVED",
                    "intent_id": "",
                    "reason": reason,
                    "trace_id": result["receipt_id"],
                    "receipt_sha256": sha256(raw.encode()).hexdigest(),
                },
            )
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
            self._connection.execute(
                "INSERT INTO control_receipts VALUES (?,?,?,?)", (key, current, reason, raw)
            )
            self._audit_chain.append(
                f"control:{key}",
                {
                    "action": "OPERATOR_CONTROL_REJECTED",
                    "intent_id": "",
                    "reason": reason,
                    "trace_id": receipt["receipt_id"],
                    "receipt_sha256": sha256(raw.encode()).hexdigest(),
                },
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

    def _canary_stage_locked(self) -> dict[str, str] | None:
        """Active recovery canary record, or None. Caller holds the lock.

        A canary is active exactly when the persisted safety state is
        REDUCED_RISK, the newest canary landing receipt
        (``stage == "CANARY"``) exists, and no promotion receipt
        references it. A bare REDUCED_RISK with no canary receipt behind
        it is not a canary and fails closed to None.
        """
        row = self._connection.execute(
            "SELECT safety_state FROM safety_current WHERE singleton = 1"
        ).fetchone()
        if row is None or row[0] != SafetyState.REDUCED.value:
            return None
        rows = self._connection.execute(
            "SELECT idempotency_key, receipt_json FROM recovery_receipts ORDER BY rowid"
        ).fetchall()
        canary: dict | None = None
        canary_key = ""
        promoted: set[str] = set()
        for key, raw in rows:
            try:
                receipt = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(receipt, dict):
                continue
            if receipt.get("stage") == "CANARY":
                canary, canary_key = receipt, key
            elif receipt.get("stage") == "PROMOTED" and receipt.get("canary_receipt_id"):
                promoted.add(receipt["canary_receipt_id"])
        if canary is None or canary.get("receipt_id") in promoted:
            return None
        return {
            "receipt_id": canary.get("receipt_id", ""),
            "idempotency_key": canary_key,
            "canary_entered_at": canary.get("canary_entered_at", ""),
            "canary_state": canary.get("canary_state", ""),
            "reason": canary.get("reason", ""),
        }

    def canary_stage(self) -> dict[str, str] | None:
        """The active staged-recovery canary record, if one exists."""
        with self._lock:
            return self._canary_stage_locked()

    def record_recovery_canary(self, key: str, reason: str, receipt: dict[str, str]) -> None:
        """Persist a staged recovery landing at REDUCED_RISK (canary).

        Mirrors :meth:`record_recovery`'s precondition (the persisted
        state must be NO_NEW_RISK) but lands at REDUCED_RISK, and requires
        the receipt to carry the canary stage binding (stage, state and
        an aware entry timestamp), so a canary stage can never exist
        without its entry record.
        """
        try:
            entered = datetime.fromisoformat(receipt.get("canary_entered_at", ""))
        except ValueError:
            entered = None
        if (
            receipt.get("stage") != "CANARY"
            or receipt.get("canary_state") != SafetyState.REDUCED.value
            or receipt.get("safety_state") != SafetyState.REDUCED.value
            or entered is None
            or entered.utcoffset() is None
        ):
            raise ValueError("Recovery canary receipt requires a staged REDUCED_RISK landing")
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
                (SafetyState.REDUCED.value,),
            )

    def record_canary_promotion(self, key: str, reason: str, receipt: dict[str, str]) -> None:
        """Persist a canary promotion to NORMAL, bound to the canary record.

        Fails closed unless an active canary stage exists and the
        promotion receipt names exactly that stage's recovery receipt.
        """
        if receipt.get("stage") != "PROMOTED" or not receipt.get("canary_receipt_id"):
            raise ValueError("Canary promotion receipt requires the canary binding")
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            stage = self._canary_stage_locked()
            if stage is None:
                raise ValueError("No active recovery canary stage can be promoted")
            if receipt["canary_receipt_id"] != stage["receipt_id"]:
                raise ValueError("Canary promotion does not match the active canary stage")
            self._connection.execute(
                "INSERT INTO recovery_receipts VALUES (?, ?, ?)",
                (key, reason, json.dumps(receipt, sort_keys=True)),
            )
            self._audit_chain.append(
                f"recovery-promotion:{key}",
                {
                    "action": "PAPER_RECOVERY_CANARY_PROMOTED",
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

    def agent_trace_observation(
        self, run_id: str
    ) -> tuple[AgentRun | None, AgentTraceBinding | None, AuditIntegrity]:
        with self._lock, self._connection:
            self._connection.execute("BEGIN")
            head = self._audit_chain.require_valid()
            run = self._agent_runs.get(run_id)
            binding = self._agent_trace_bindings.get(run_id, run)
            if self._audit_chain.require_valid() != head:
                raise ValueError("Original audit prefix changed during lookup")
            return run, binding, head

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

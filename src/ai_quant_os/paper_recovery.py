"""Server-owned paper uncertainty triage. No recovery or broker dispatch authority."""

import json
import re
from contextlib import contextmanager
from threading import RLock

from ai_quant_os.broker import BrokerOutcomeUnknown, BrokerUnavailable
from ai_quant_os.execution import PaperExecutionGateway
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.risk import RiskKernel, SafetyState


def validate_recovery_ids(incident_id: str, order_id: str) -> None:
    for value in (incident_id, order_id):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("Recovery identifiers must be bounded opaque identifiers")


def require_completed_triage(events: list[dict[str, str]]) -> None:
    """Require durable review-ready evidence before the separate manual recovery gate."""
    admissions = {}
    attempts = {}
    for event in events:
        if event.get("action") == "PAPER_RECOVERY_CONTAINED":
            admissions[event["trace_id"]] = event["order_id"]
        elif event.get("action") == "PAPER_RECOVERY_ATTEMPT":
            result = json.loads(event["evidence"])
            incident = event["trace_id"]
            attempt = result["attempt"]
            if (
                type(attempt) is not int
                or not 1 <= attempt <= 20
                or result["incident_id"] != incident
            ):
                raise ValueError("Invalid paper recovery evidence")
            if incident not in attempts or attempt > attempts[incident]["attempt"]:
                attempts[incident] = result
    for incident, order_id in admissions.items():
        result = attempts.get(incident)
        if (
            result is None
            or result["order_id"] != order_id
            or result["stage"] != "REVIEW_REQUIRED"
            or result["positions_match"] is not True
            or result["promotion_allowed"] is not False
        ):
            raise ValueError("Unfinished paper triage blocks manual safety recovery")


class PaperRecoveryService:
    """Bind to the application's existing kernel, gateway and durable state store.

    Activity receipts are immutable per attempt. A retry after a lost activity
    acknowledgement returns the receipt; only a new attempt performs a new lookup.
    """

    def __init__(
        self,
        execution: PaperExecutionGateway,
        risk: RiskKernel,
        state: PaperStateStore,
        *,
        control_lock=None,
    ):
        self._execution = execution
        self._risk = risk
        self._state = state
        self._lock = control_lock if control_lock is not None else RLock()
        self._risk.set_safety_state(self._highest_state())

    def _highest_state(self) -> SafetyState:
        return max(
            (self._risk.safety_state, self._state.safety_state()), key=list(SafetyState).index
        )

    @contextmanager
    def _fail_closed(self):
        try:
            yield
        except Exception:
            self._risk.set_safety_state(SafetyState.HALT)
            raise

    def _stop(self, incident_id: str, order_id: str, *, halt: bool = False) -> None:
        target = max(
            (self._highest_state(), SafetyState.HALT if halt else SafetyState.NO_NEW_RISK),
            key=list(SafetyState).index,
        )
        try:
            self._state.record_order_uncertainty(order_id)
            if target in {SafetyState.FLATTEN_ONLY, SafetyState.HALT}:
                key = f"paper-recovery:{incident_id}:stop:{target.value}"
                if self._state.control_receipt(key) is None:
                    self._state.record_control(
                        key,
                        target,
                        "PAPER_UNCERTAINTY_TRIAGE",
                        {"receipt_id": key, "safety_state": target.value},
                    )
            self._risk.set_safety_state(target)
        except Exception:
            # Persistence failure cannot permit further risk in this process.
            # No claim is made that a failed disk write is restart-durable.
            self._risk.set_safety_state(SafetyState.HALT)
            raise

    def contain(self, incident_id: str, order_id: str) -> dict[str, str]:
        validate_recovery_ids(incident_id, order_id)
        with self._lock:
            order = next(
                (item for item in self._execution.orders if item.order_id == order_id), None
            )
            if order is None or order.command.broker_route != "PAPER":
                raise ValueError("Recovery requires a server-owned PAPER order")
            event = {
                "action": "PAPER_RECOVERY_CONTAINED",
                "trace_id": incident_id,
                "intent_id": order.intent_id,
                "order_id": order_id,
                "reason": "BROKER_OUTCOME_UNCERTAIN",
            }
            key = f"paper-recovery:{incident_id}:contained"
            try:
                prior = self._state.audit_event(key)
                if prior is not None and prior != event:
                    raise ValueError("Recovery incident already belongs to another order")
                self._stop(incident_id, order_id)
                self._state.record_event(key, event)
                return event
            except Exception:
                self._risk.set_safety_state(SafetyState.HALT)
                raise

    def reconcile(self, incident_id: str, order_id: str, attempt: int) -> dict:
        validate_recovery_ids(incident_id, order_id)
        if type(attempt) is not int or not 1 <= attempt <= 20:
            raise ValueError("Recovery attempt must be between 1 and 20")
        with self._lock, self._fail_closed():
            admission = self._state.audit_event(f"paper-recovery:{incident_id}:contained")
            if admission is None or admission["order_id"] != order_id:
                raise ValueError("Recovery incident must be contained first")
            self._stop(incident_id, order_id)
            key = f"paper-recovery:{incident_id}:attempt:{attempt}"
            prior = self._state.audit_event(key)
            if prior is not None:
                return json.loads(prior["evidence"])
            try:
                order = self._execution.reconcile_order(order_id)
                positions = self._execution.compare_paper_positions()
                resolved = order.status in {"ACCEPTED", "REJECTED", "FILLED", "PARTIALLY_FILLED"}
                stage = "REVIEW_REQUIRED" if resolved and positions.matches else "WAITING_RETRY"
                if not positions.matches:
                    self._stop(incident_id, order_id, halt=True)
                result = {
                    "incident_id": incident_id,
                    "order_id": order_id,
                    "attempt": attempt,
                    "stage": stage,
                    "order_status": order.status,
                    "positions_match": positions.matches,
                    "safety_state": self._risk.safety_state.value,
                    "promotion_allowed": False,
                }
            except (BrokerOutcomeUnknown, BrokerUnavailable):
                result = {
                    "incident_id": incident_id,
                    "order_id": order_id,
                    "attempt": attempt,
                    "stage": "WAITING_RETRY",
                    "order_status": "UNCONFIRMED",
                    "positions_match": None,
                    "safety_state": self._risk.safety_state.value,
                    "promotion_allowed": False,
                }
            try:
                self._state.record_event(
                    key,
                    {
                        "action": "PAPER_RECOVERY_ATTEMPT",
                        "trace_id": incident_id,
                        "intent_id": admission["intent_id"],
                        "evidence": json.dumps(result, sort_keys=True, separators=(",", ":")),
                    },
                )
            except Exception:
                self._risk.set_safety_state(SafetyState.HALT)
                raise
            return result

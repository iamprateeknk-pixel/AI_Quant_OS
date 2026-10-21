"""Deterministic owner-bound health checks and durable quarantine restoration."""

import asyncio
import json
import logging
import sqlite3
from dataclasses import asdict
from threading import Lock
from uuid import uuid4

from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.risk import SafetyState

LOGGER = logging.getLogger(__name__)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class HealthWatchdog:
    def __init__(
        self,
        store,
        state,
        risk,
        execution,
        registry,
        graph_receipts,
        quotes,
        clock,
        *,
        control_lock=None,
    ):
        self.store, self.state, self.risk = store, state, risk
        self.execution, self.registry = execution, registry
        self.graph_receipts, self.quotes, self.clock = graph_receipts, quotes, clock
        self._lock = control_lock if control_lock is not None else Lock()
        self._cursor = ""
        self._last_tick = None
        self.last_error = None
        self._known_ids = set()

    def _apply(self, incident, *, apply_safety=True):
        if incident.quarantine:
            self.registry.quarantine(
                incident.agent_id, incident.agent_version, incident.manifest_ref
            )
        target = max(
            (self.risk.safety_state, self.state.safety_state(), SafetyState(incident.safety_state)),
            key=list(SafetyState).index,
        )

        if not apply_safety:
            return

        def persist_stop():
            if self.state.audit_integrity().valid and list(SafetyState).index(
                self.state.safety_state()
            ) < list(SafetyState).index(target):
                key = f"health-stop:{incident.incident_id}:{uuid4()}"
                self.state.record_control(
                    key, target, incident.code, {"receipt_id": key, "safety_state": target.value}
                )

        try:
            self.risk.set_safety_state(target, before_change=persist_stop)
        except Exception:
            self.risk.set_safety_state(SafetyState.HALT)
            raise

    def restore(self):
        with self._lock:
            try:
                open_ids = {item.incident_id for item in self.store.open_incidents()}
                for incident in self.store.incidents():
                    self._apply(incident, apply_safety=incident.incident_id in open_ids)
                    self._known_ids.add(incident.incident_id)
            except Exception:
                self.risk.set_safety_state(SafetyState.HALT)
                raise

    def close_quote_incident(self, incident_id, *, command_id, reason):
        with self._lock:
            try:
                incident = next(
                    (item for item in self.store.incidents() if item.incident_id == incident_id),
                    None,
                )
                if incident is None:
                    raise ValueError("Unknown health incident")
                if incident.code not in {"QUOTE_STALE", "QUOTE_FUTURE", "QUOTE_UNTRUSTED"}:
                    raise ValueError("Only quote incidents may be closed")
                now = self.clock()
                from ai_quant_os.contracts import _require_aware

                _require_aware(now)
                if now.utcoffset().total_seconds() != 0:
                    raise ValueError("Watchdog clock must be UTC")
                matching = [
                    quote for quote in self.quotes() if quote.instrument_id == incident.subject
                ]
                if not matching or any(
                    not quote.trusted
                    or (now - quote.observed_at).total_seconds() < 0
                    or (now - quote.observed_at).total_seconds() > 5
                    for quote in matching
                ):
                    raise ValueError("Closure requires a fresh trusted quote")
                return self.store.close_incident(
                    incident_id, closed_at=now, command_id=command_id, reason=reason
                )
            except ValueError:
                raise
            except Exception:
                self.last_error = "WATCHDOG_VERIFICATION_OR_STORAGE_FAILURE"
                self._failure_stop()
                raise

    @property
    def pending_graph_scan(self):
        return bool(self._cursor)

    def verify_admission(self):
        with self._lock:
            try:
                self.store.integrity()
            except Exception:
                self.last_error = "WATCHDOG_VERIFICATION_OR_STORAGE_FAILURE"
                self._failure_stop()
                raise

    def observe_receipt(self, receipt):
        with self._lock:
            try:
                if receipt.status in {"FAILED", "TIMEOUT", "INVALID", "UNCERTAIN"}:
                    self._observe(
                        "AGENT_" + receipt.status,
                        receipt.run_id,
                        json.loads(receipt.model_dump_json()),
                        self.clock(),
                        receipt=receipt,
                    )
            except Exception:
                self.last_error = "WATCHDOG_VERIFICATION_OR_STORAGE_FAILURE"
                self._failure_stop()
                raise

    def _observe(self, code, subject, evidence, now, *, receipt=None):
        incident = HealthIncident(
            code,
            subject,
            canonical(evidence),
            now,
            receipt.agent_id if receipt else None,
            receipt.agent_version if receipt else None,
            receipt.manifest_ref if receipt else None,
        )
        try:
            incident = self.store.record(incident)
        except Exception:
            if incident.quarantine:
                self.registry.quarantine(
                    incident.agent_id, incident.agent_version, incident.manifest_ref
                )
            self.risk.set_safety_state(SafetyState.HALT)
            raise
        self._apply(incident)
        if incident.incident_id not in self._known_ids:
            self._known_ids.add(incident.incident_id)
            LOGGER.warning(
                canonical(
                    {
                        "event": "health_incident",
                        "incident_id": incident.incident_id,
                        "code": incident.code,
                        "safety_state": self.risk.safety_state.value,
                    }
                )
            )

    def _failure_stop(self):
        self.risk.set_safety_state(SafetyState.HALT)
        if self.state.audit_integrity().valid and self.state.safety_state() != SafetyState.HALT:
            key = f"health-worker-failure:{uuid4()}"
            self.state.record_control(
                key,
                SafetyState.HALT,
                "WATCHDOG_FAILURE",
                {"receipt_id": key, "safety_state": "HALT"},
            )

    def step(self):
        with self._lock:
            try:
                now = self.clock()
                # Validate source clock even when no failure observation is emitted.
                from ai_quant_os.contracts import _require_aware

                _require_aware(now)
                if now.utcoffset().total_seconds() != 0:
                    raise ValueError("Watchdog clock must be UTC")
                self.store.integrity()
                audit = self.state.audit_integrity()
                if not audit.valid:
                    self._observe("AUDIT_INVALID", "paper-audit", asdict(audit), now)
                    self._last_tick = now
                    return self.status()
                if self._last_tick is not None and now < self._last_tick:
                    self._observe(
                        "CLOCK_REGRESSION",
                        "watchdog-clock",
                        {"previous": self._last_tick.isoformat(), "current": now.isoformat()},
                        now,
                    )
                for order in self.execution.orders:
                    if order.status in {"DISPATCHING", "UNKNOWN"}:
                        self._observe(
                            "BROKER_UNCONFIRMED",
                            order.order_id,
                            {
                                "order_id": order.order_id,
                                "intent_id": order.intent_id,
                                "broker_route": order.command.broker_route,
                            },
                            now,
                        )
                comparison = self.execution.compare_paper_positions()
                if not comparison.matches:
                    self._observe(
                        "POSITION_MISMATCH",
                        "paper-positions",
                        {
                            "local_only": comparison.local_only_fill_ids,
                            "broker_only": comparison.broker_only_fill_ids,
                            "conflicting": comparison.conflicting_fill_ids,
                        },
                        now,
                    )
                for quote in self.quotes():
                    age = (now - quote.observed_at).total_seconds()
                    code = (
                        "QUOTE_UNTRUSTED"
                        if not quote.trusted
                        else "QUOTE_FUTURE"
                        if age < 0
                        else "QUOTE_STALE"
                        if age > 5
                        else None
                    )
                    if code:
                        self._observe(
                            code,
                            quote.instrument_id,
                            {
                                "instrument_id": quote.instrument_id,
                                "bid": str(quote.bid),
                                "ask": str(quote.ask),
                                "observed_at": quote.observed_at.isoformat(),
                                "world_state_version": quote.world_state_version,
                                "trusted": quote.trusted,
                            },
                            now,
                        )
                try:
                    receipts = self.graph_receipts(self._cursor)
                except (ValueError, KeyError, sqlite3.Error) as error:
                    self._observe(
                        "GRAPH_INTEGRITY_FAILED",
                        "agent-graph",
                        {"exception_type": type(error).__name__},
                        now,
                    )
                    receipts = ()
                for receipt in receipts:
                    if receipt.status in {"FAILED", "TIMEOUT", "INVALID", "UNCERTAIN"}:
                        self._observe(
                            "AGENT_" + receipt.status,
                            receipt.run_id,
                            json.loads(receipt.model_dump_json()),
                            now,
                            receipt=receipt,
                        )
                self._cursor = receipts[-1].run_id if len(receipts) == 64 else ""
                self._last_tick = now
                self.last_error = None
                return self.status()
            except Exception:
                self.last_error = "WATCHDOG_VERIFICATION_OR_STORAGE_FAILURE"
                self._failure_stop()
                raise

    def status(self):
        incidents = self.store.incidents()
        open_ids = {item.incident_id for item in self.store.open_incidents()}
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "state": "ERROR" if self.last_error else "MONITORING",
            "open_incidents": len(open_ids),
            "quarantined_versions": sorted(
                {f"{i.agent_id}@{i.agent_version}" for i in incidents if i.quarantine}
            ),
            "last_tick": self._last_tick.isoformat() if self._last_tick else None,
            "last_error": self.last_error,
            "promotion_allowed": False,
        }


async def owned_health_call(method, *args):
    task = asyncio.create_task(asyncio.to_thread(method, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
            except Exception as error:
                # The owning check has already halted/recorded its failure.
                # A late disk failure must not revive a cancelled monitor loop.
                raise cancellation from error
        raise


async def run_health_watchdog(worker):
    delay = 1
    while True:
        try:
            await owned_health_call(worker.step)
        except Exception:
            # step() records a sanitized error and halts; retry with bounded backoff.
            pass
        delay = min(60, delay * 2) if worker.last_error else 1
        await asyncio.sleep(delay)

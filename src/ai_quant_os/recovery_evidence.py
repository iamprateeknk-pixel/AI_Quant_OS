"""Recovery quorum evidence production for the paper recovery path (V3.2 section 13).

Phase 2 wired :mod:`ai_quant_os.recovery_verifier` into the operator
paper-recovery endpoint in shadow mode, but the endpoint held almost no
evidence, so every shadow verdict was REJECTED naming nearly every gate.
This module is the producer that closes that gap honestly: given the live
paper components it computes, from the paper system itself in this call,
the gate results, evidence references and independent-replay outcome that
``verify_recovery`` consumes. A gate is True only when the evidence below
was actually computed; every section fails closed (any exception makes
that gate False, never a propagated crash).

Gate-by-gate production:

* ``agent_health`` -- a health store was supplied (the endpoint passes the
  health watchdog's store, so a store exists exactly when the watchdog is
  wired) AND the store reports no open incidents in this call.
* ``data_state`` -- the completed-triage check
  (:func:`ai_quant_os.paper_recovery.require_completed_triage`) passes over
  the live audit chain, the audit chain integrity verifies, and a snapshot
  hash of the recovery-relevant state (safety state, audit head, ledger
  digest, bound to the receipt id) was computed and recorded as a ref.
* ``repair_verification`` -- from the completed-triage artifact only: at
  least one contained incident has a latest triage attempt artifact that
  verifies as completed (stage ``REVIEW_REQUIRED``, positions matched,
  promotion not allowed, order binding intact) under the same parsing
  ``require_completed_triage`` uses. A recovery path with no triage
  artifact at all has nothing to verify a repair against, so the gate is
  False there -- it is never inferred from the absence of incidents.
* ``risk_execution`` -- the risk kernel's live safety state equals the
  state store's persisted safety state AND the digest of the kernel's
  limits equals the digest of the active risk policy's limits. Both sides
  are computed in this call; no limit value is assumed.
* ``broker_truth`` -- computed independently here, before any mutation:
  no order in the gateway is in an unresolved status (``PENDING``,
  ``DISPATCHING`` or ``UNKNOWN``) AND
  ``execution.compare_paper_positions().matches`` is True in this call.
  This runs after every snapshot/replay comparison below, because on a
  mismatch ``compare_paper_positions`` escalates uncertainty exactly as
  ``recover_paper_safety`` itself would; the evidence snapshot is taken
  first so that side effect cannot contaminate it.
* ``security`` -- exactly the explicit ``security_verified`` attestation
  argument. The producer never defaults it: the endpoint passes True only
  because the recovery handler sits behind BOTH the
  ``require_control_operator`` and ``require_recovery`` dependencies, and
  app construction refuses to start when the operator and recovery tokens
  are equal, so the two checks are backed by distinct credentials.
  Callers without that route guarantee must pass False.
* ``canary`` -- produced only when the caller supplies the staged
  canary record (``PaperStateStore.canary_stage()``) for a recovery that
  landed at REDUCED_RISK through the staged path, together with the
  evaluation instant and the policy's minimum soak
  (``PaperRiskPolicy.recovery_canary_min_soak_seconds``). The gate
  passes only when the persisted and kernel safety states are both
  REDUCED_RISK, at least the minimum soak has elapsed since the
  recorded canary entry, and no health incident was detected at or
  after entry. The recovery endpoint's shadow verdict is computed
  before the staged landing, so this gate is False there by
  construction; it first becomes producible at the promotion step,
  which re-runs this producer against the landed canary. Without a
  stage record the gate is False -- never assumed.
* ``resume_controller`` -- True only in the structural sense that the
  verdict is computed and recorded by the deterministic endpoint code
  itself (this producer runs synchronously inside the ``recover_paper``
  handler; no LLM or agent participates in the resume path). The producer
  records a controller identity ref naming that code path. No stronger
  claim is made.

Independent replay: when a paper DB path is configured, the state store's
SQLite file is copied with the sqlite3 backup API while holding the state
store's lock (the store's writes are the ones that move the audit head and
safety state; the backup is a transactionally consistent snapshot of the
file). The copy -- never the live connection used for mutation -- is then
opened through ``PaperStateStore`` and ``PaperOrderStore``: construction
itself verifies the audit chain, and the producer additionally compares
the copy's audit head/count, safety state and recomputed order/fill
ledger digest against the live values read at the start of this call.
Replay passes only if the copy verifies AND matches live on all four.
With no on-disk paper DB (``paper_db_path`` is None or not a file) the
replay is False: fail-closed, never assumed.
"""

import json
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from ai_quant_os.execution import (
    PaperExecutionGateway,
    PaperOrder,
    _decode_order,
    _with_fill_status,
)
from ai_quant_os.health_store import HealthStore
from ai_quant_os.order_store import PaperOrderStore
from ai_quant_os.paper_recovery import require_completed_triage
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.portfolio import PaperFill
from ai_quant_os.recovery_verifier import QUORUM_GATES
from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState
from ai_quant_os.risk_policy import PaperRiskPolicy

#: Statuses that mean an order's broker outcome is not yet resolved. The
#: same set blocks ``PaperExecutionGateway.recover_paper_safety``.
_UNRESOLVED_ORDER_STATUSES = frozenset({"PENDING", "DISPATCHING", "UNKNOWN"})

#: Identity of the deterministic resume controller for this path. Recorded
#: as an evidence ref; see the module docstring for the narrow claim.
_RESUME_CONTROLLER_REF = "resume-controller:ai-quant-os.api:recover_paper"


@dataclass(frozen=True)
class RecoveryEvidence:
    """Producer output consumed by ``recovery_verifier.verify_recovery``."""

    gate_results: dict[str, bool]
    evidence_refs: tuple[str, ...]
    independent_replay_passed: bool


def unavailable_evidence(receipt_id: str) -> RecoveryEvidence:
    """All-gates-failed evidence for when production itself is impossible.

    Used by the endpoint as a fail-closed fallback so a producer failure
    records the Phase 2 shadow posture (every gate failed, no replay)
    instead of changing the recovery accept/refuse behaviour.
    """
    return RecoveryEvidence(
        gate_results={gate: False for gate in QUORUM_GATES},
        evidence_refs=(f"paper-recovery:{receipt_id}",),
        independent_replay_passed=False,
    )


def _encode_value(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported ledger value: {type(value)}")


def _ledger_digest(orders: tuple[PaperOrder, ...], fills: tuple[PaperFill, ...]) -> str:
    """SHA-256 over the canonical order/fill ledger (orders carry the same
    fill-adjusted status the gateway reports, so a copy recomputed through
    the store classes digests identically to the live gateway view)."""
    payload = {
        "orders": [
            {
                "order_id": order.order_id,
                "intent_id": order.intent_id,
                "risk_id": order.risk_id,
                "status": order.status,
                "broker_order_id": order.broker_order_id,
                "command": asdict(order.command),
            }
            for order in sorted(orders, key=lambda item: item.order_id)
        ],
        "fills": [asdict(fill) for fill in sorted(fills, key=lambda item: item.fill_id)],
    }
    canonical = json.dumps(payload, default=_encode_value, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


def _limits_digest(limits: RiskLimits) -> str:
    canonical = json.dumps(
        {
            "max_order_exposure": str(limits.max_order_exposure),
            "max_portfolio_exposure": str(limits.max_portfolio_exposure),
            "max_quote_age_seconds": limits.max_quote_age_seconds,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


def _verified_triage_attempts(events: list[dict[str, str]]) -> list[dict]:
    """Latest triage attempts that verify as completed, parsed exactly the
    way ``require_completed_triage`` parses them. Raises on malformed
    evidence, like the check it mirrors; callers fail closed."""
    admissions: dict[str, str] = {}
    attempts: dict[str, dict] = {}
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
    verified = []
    for incident, order_id in admissions.items():
        result = attempts.get(incident)
        if (
            result is not None
            and result["order_id"] == order_id
            and result["stage"] == "REVIEW_REQUIRED"
            and result["positions_match"] is True
            and result["promotion_allowed"] is False
        ):
            verified.append(result)
    return verified


def _independent_replay(
    *,
    state_store: PaperStateStore,
    paper_db_path: Path | None,
    live_safety: SafetyState | None,
    live_head_hash: str | None,
    live_event_count: int | None,
    live_ledger_digest: str | None,
) -> tuple[bool, str | None]:
    """Replay the paper DB from a backup copy; see the module docstring.

    Returns ``(passed, replay_ref)``. The ref (a hash of the copy's audit
    head, safety state and ledger digest) is returned whenever the copy
    was opened and recomputed, pass or fail; both are None/False when no
    honest replay could run at all.
    """
    if paper_db_path is None or not paper_db_path.is_file():
        return False, None
    if live_safety is None or live_head_hash is None or live_ledger_digest is None:
        return False, None
    try:
        with tempfile.TemporaryDirectory(prefix="recovery-replay-") as scratch:
            copy_path = Path(scratch) / "paper-replay.sqlite3"
            destination = sqlite3.connect(str(copy_path))
            try:
                # Hold the store's own lock for the backup so its writes
                # cannot move the audit head mid-copy; only the raw backup
                # call happens under the lock (the store's public reads
                # take the same non-reentrant lock and must stay outside).
                with state_store._lock:
                    state_store._connection.backup(destination)
            finally:
                destination.close()
            # Opening the copy through the store classes verifies its audit
            # chain during construction; a tampered copy raises here.
            copy_state = PaperStateStore(copy_path)
            copy_orders = PaperOrderStore(copy_path)
            try:
                integrity = copy_state.audit_integrity()
                copy_fills = copy_orders.all_fills()
                copy_order_values = tuple(
                    _with_fill_status(_decode_order(raw), copy_fills)
                    for raw in copy_orders.all_orders()
                )
                copy_digest = _ledger_digest(copy_order_values, copy_fills)
                copy_safety = copy_state.safety_state()
            finally:
                copy_state._connection.close()
                copy_orders._connection.close()
            replay_ref = _hash_payload(
                {
                    "audit_head": integrity.head_hash,
                    "ledger_digest": copy_digest,
                    "safety_state": copy_safety.value,
                }
            )
            passed = (
                integrity.valid
                and integrity.head_hash == live_head_hash
                and integrity.event_count == live_event_count
                and copy_safety == live_safety
                and copy_digest == live_ledger_digest
            )
            return passed, replay_ref
    except Exception:
        return False, None


def _canary_gate(
    *,
    canary_stage: dict[str, str],
    now: datetime | None,
    min_soak_seconds: int,
    store_safety: SafetyState | None,
    kernel_safety: SafetyState | None,
    health_store: HealthStore | None,
) -> tuple[bool, str | None]:
    """Evaluate the section 13 canary gate against a staged landing record.

    Passes only when every soak criterion holds (see the module
    docstring): persisted and kernel state both REDUCED_RISK, the stage
    record's entry time at least ``min_soak_seconds`` before ``now``,
    and no health incident detected at or after entry. Returns the
    verdict plus an evidence ref binding the evaluated facts; the ref
    is produced whenever a stage record was supplied and could be read,
    pass or fail. An unreadable record yields ``(False, None)``.
    """
    try:
        entered = datetime.fromisoformat(canary_stage["canary_entered_at"])
        receipt_id = canary_stage["receipt_id"]
    except (KeyError, TypeError, ValueError):
        return False, None
    if entered.utcoffset() is None or now is None or now.utcoffset() is None:
        return False, None
    elapsed = (now - entered).total_seconds()
    incidents_during: int | None = None
    if health_store is not None:
        try:
            incidents_during = sum(
                1 for incident in health_store.incidents() if incident.detected_at >= entered
            )
        except Exception:
            incidents_during = None
    ref = "recovery-canary:sha256:" + _hash_payload(
        {
            "receipt_id": receipt_id,
            "canary_entered_at": entered.isoformat(),
            "elapsed_seconds": int(elapsed),
            "incidents_during_canary": incidents_during,
            "min_soak_seconds": min_soak_seconds,
        }
    )
    passed = bool(
        store_safety is SafetyState.REDUCED
        and kernel_safety is SafetyState.REDUCED
        and elapsed >= min_soak_seconds
        and incidents_during == 0
    )
    return passed, ref


def produce_recovery_evidence(
    *,
    state_store: PaperStateStore,
    execution: PaperExecutionGateway,
    risk: RiskKernel,
    health_store: HealthStore | None,
    paper_db_path: Path | None,
    receipt_id: str,
    security_verified: bool,
    risk_policy: PaperRiskPolicy,
    canary_stage: dict[str, str] | None = None,
    canary_now: datetime | None = None,
    canary_min_soak_seconds: int = 0,
) -> RecoveryEvidence:
    """Compute the recovery quorum evidence from the live paper system.

    ``security_verified`` is an explicit attestation with no default; see
    the module docstring for exactly what passing True claims. Every gate
    section is computed independently and fails closed on any error.
    """
    refs: list[str] = []

    # --- live snapshot (read first; nothing below may contaminate it) ------
    events: list[dict[str, str]] | None = None
    try:
        events = state_store.audit_events()
    except Exception:
        events = None
    integrity = None
    try:
        integrity = state_store.audit_integrity()
    except Exception:
        integrity = None
    store_safety: SafetyState | None = None
    try:
        store_safety = state_store.safety_state()
    except Exception:
        store_safety = None
    kernel_safety: SafetyState | None = None
    try:
        kernel_safety = risk.safety_state
    except Exception:
        kernel_safety = None
    live_orders: tuple[PaperOrder, ...] | None = None
    live_fills: tuple[PaperFill, ...] | None = None
    ledger_digest: str | None = None
    try:
        live_orders = execution.orders
        live_fills = execution.fills
        ledger_digest = _ledger_digest(live_orders, live_fills)
    except Exception:
        live_orders = None
        live_fills = None
        ledger_digest = None

    snapshot_hash: str | None = None
    if store_safety is not None and integrity is not None and ledger_digest is not None:
        snapshot_hash = _hash_payload(
            {
                "receipt_id": receipt_id,
                "safety_state": store_safety.value,
                "audit_head": integrity.head_hash,
                "audit_event_count": integrity.event_count,
                "ledger_digest": ledger_digest,
            }
        )
        refs.append(f"paper-state-snapshot:sha256:{snapshot_hash}")
    if integrity is not None:
        refs.append(f"audit-chain:sha256:{integrity.head_hash}")

    # --- triage: data_state and repair_verification share the artifact ----
    triage_ok = False
    verified_attempts: list[dict] = []
    if events is not None:
        try:
            require_completed_triage(events)
            triage_ok = True
        except Exception:
            triage_ok = False
        try:
            verified_attempts = _verified_triage_attempts(events)
        except Exception:
            verified_attempts = []
    if verified_attempts:
        refs.append(f"paper-triage:sha256:{_hash_payload(verified_attempts)}")
    data_state = bool(
        triage_ok and integrity is not None and integrity.valid and snapshot_hash is not None
    )
    repair_verification = bool(triage_ok and verified_attempts)

    # --- risk_execution: kernel vs persisted state, policy vs kernel ------
    risk_execution = False
    kernel_limits = getattr(risk, "_limits", None)
    if (
        store_safety is not None
        and kernel_safety is not None
        and isinstance(kernel_limits, RiskLimits)
    ):
        try:
            policy_limits = risk_policy.risk_limits()
            risk_execution = bool(
                kernel_safety == store_safety
                and kernel_limits == policy_limits
                and _limits_digest(kernel_limits) == _limits_digest(policy_limits)
            )
            refs.append(f"risk-policy:sha256:{risk_policy.digest}")
        except Exception:
            risk_execution = False

    # --- agent_health -------------------------------------------------------
    agent_health = False
    if health_store is not None:
        try:
            open_incidents = health_store.open_incidents()
            refs.append(
                "agent-health:sha256:"
                + _hash_payload(sorted(item.incident_id for item in open_incidents))
            )
            agent_health = len(open_incidents) == 0
        except Exception:
            agent_health = False

    # --- canary: staged REDUCED_RISK landing + soak -------------------------
    canary = False
    if canary_stage is not None:
        canary, canary_ref = _canary_gate(
            canary_stage=canary_stage,
            now=canary_now,
            min_soak_seconds=canary_min_soak_seconds,
            store_safety=store_safety,
            kernel_safety=kernel_safety,
            health_store=health_store,
        )
        if canary_ref is not None:
            refs.append(canary_ref)

    # --- independent replay (before broker_truth: see module docstring) ---
    replay_passed, replay_ref = _independent_replay(
        state_store=state_store,
        paper_db_path=paper_db_path,
        live_safety=store_safety,
        live_head_hash=integrity.head_hash if integrity is not None else None,
        live_event_count=integrity.event_count if integrity is not None else None,
        live_ledger_digest=ledger_digest,
    )
    if replay_ref is not None:
        refs.append(f"independent-replay:sha256:{replay_ref}")

    # --- broker_truth (last: a mismatch escalates, like the recovery call) -
    broker_truth = False
    if live_orders is not None:
        try:
            unresolved = any(order.status in _UNRESOLVED_ORDER_STATUSES for order in live_orders)
            broker_truth = bool(
                not unresolved and execution.compare_paper_positions().matches is True
            )
        except Exception:
            broker_truth = False

    # --- structural gates ----------------------------------------------------
    if snapshot_hash is not None:
        refs.append(_RESUME_CONTROLLER_REF)

    gate_results = {
        "agent_health": agent_health,
        "repair_verification": repair_verification,
        "data_state": data_state,
        "risk_execution": risk_execution,
        "broker_truth": broker_truth,
        "security": security_verified is True,
        "canary": canary,
        "resume_controller": True,
    }
    return RecoveryEvidence(
        gate_results=gate_results,
        evidence_refs=tuple(refs),
        independent_replay_passed=replay_passed,
    )

"""Promotion lifecycle consumer: the hook that consumes the promotion gate.

This module is the lifecycle owner for strategy promotion. It is the
only path that turns a strategy paper-active, and it does so strictly
by consuming :func:`ai_quant_os.strategy_promotion.evaluate_promotion`:
the gate is never reimplemented here, and no code path in this module
marks a strategy ``PAPER_ACTIVE`` -- or builds a ``PromotionDecision``
with ``approved_by_policy`` true -- unless ``evaluate_promotion``
returned ``promoted=True`` for that same call.

Candidate binding: a promotion request names a strategy, while the
evidence contract names a candidate. This lifecycle binds them
one-to-one -- ``PromotionDecision.candidate_id`` is the request's
``strategy_id`` -- and every registry record carries both
``strategy_id`` and ``candidate_id`` (always equal under this binding)
plus the ``hypothesis_id`` the promotion was requested for.

Honesty rules for the recorded evidence: the decision's ``offline_eval``
slot carries the fortress leg and nothing else -- ``PASS`` when the
gate's fortress leg passed, ``FAIL`` when a verdict exists but failed
or was issued for a different hypothesis, and ``UNAVAILABLE`` when no
verdict was supplied. The replay, adversarial, shadow and canary slots
are ``NOT_RUN`` with an explicit reason: they are not part of the
fortress-plus-council promotion gate, and this module never fabricates
results for them.

Idempotency: a repeat request whose fortress verdict reference and
council outcome (council name, verdict and proposal id, together with
the hypothesis binding) are already recorded for the strategy returns
the recorded outcome -- the same evidence reference, no duplicate
attempt record, no duplicate status flip. A genuinely new attempt
(different evidence) records a new attempt. A strategy that is already
``PAPER_ACTIVE`` stays ``PAPER_ACTIVE``.
"""

import json
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.councils import VERDICTS
from ai_quant_os.promotion_contracts import PromotionDecision, PromotionEvaluation
from ai_quant_os.promotion_store import PromotionEvidenceStore
from ai_quant_os.strategy_promotion import (
    STRATEGY_COUNCIL,
    PromotionGateDecision,
    PromotionRequest,
    evaluate_promotion,
)
from ai_quant_os.validation_fortress import FortressVerdict

#: Lifecycle status of a strategy that has never been promoted.
NOT_PROMOTED = "NOT_PROMOTED"
#: Lifecycle status of a strategy the promotion gate has promoted.
PAPER_ACTIVE = "PAPER_ACTIVE"
_STATUSES = (NOT_PROMOTED, PAPER_ACTIVE)

_ATTEMPT_REF = re.compile(r"promotion-attempt:[a-f0-9]{64}")
_EVIDENCE_REF = re.compile(r"promotion-decision:[a-f0-9]{64}")
_SHA256_HEX = re.compile(r"[a-f0-9]{64}")
_MAX_RECORDS = 1000
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_RECORD_BYTES = 1024 * 1024

_NOT_GATE_EVALUATION_REASON = "not part of the fortress+council promotion gate"


def _canonical_number(value: object) -> object:
    """JSON-stable form of a fortress metric: finite floats stay numeric."""
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    if math.isfinite(number):
        return number
    return f"non-finite:{number}"


def fortress_verdict_ref(verdict: FortressVerdict) -> str:
    """Content-hash reference for a fortress verdict.

    Evidence references in this repository are content hashes (the
    fortress verdict itself carries no id), so the reference is the
    SHA-256 of a canonical encoding of the verdict: its pass flag, its
    per-gate results and its metrics.
    """
    payload = {
        "passed": bool(verdict.passed),
        "gates": [
            {
                "gate": gate.gate,
                "passed": bool(gate.passed),
                "metric": _canonical_number(gate.metric),
                "threshold": gate.threshold,
                "reason": gate.reason,
            }
            for gate in verdict.gates
        ],
        "metrics": {
            key: _canonical_number(verdict.metrics[key]) for key in sorted(verdict.metrics)
        },
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


@dataclass(frozen=True)
class PromotionAttempt:
    """One recorded promotion attempt and the status it left behind."""

    strategy_id: str
    candidate_id: str
    hypothesis_id: str
    fortress_hypothesis_id: str | None
    fortress_verdict_ref: str | None
    fortress_passed: bool
    council_name: str
    council_verdict: str
    council_proposal_id: str
    gate_promoted: bool
    gate_reasons: tuple[str, ...]
    decision: str
    approved_by_policy: bool
    evidence_ref: str
    status_after: str
    attempted_at: str

    def __post_init__(self) -> None:
        for label, value in (
            ("strategy_id", self.strategy_id),
            ("candidate_id", self.candidate_id),
            ("hypothesis_id", self.hypothesis_id),
            ("council_name", self.council_name),
            ("council_proposal_id", self.council_proposal_id),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"Promotion attempt {label} is required")
        if self.candidate_id != self.strategy_id:
            raise ValueError("Promotion attempt candidate must bind to the strategy")
        if self.fortress_hypothesis_id is not None and (
            type(self.fortress_hypothesis_id) is not str or not self.fortress_hypothesis_id
        ):
            raise ValueError("Promotion attempt fortress hypothesis id is malformed")
        if self.fortress_verdict_ref is not None and (
            type(self.fortress_verdict_ref) is not str
            or not _SHA256_HEX.fullmatch(self.fortress_verdict_ref)
        ):
            raise ValueError("Promotion attempt fortress verdict ref must be a SHA-256 hex digest")
        for label, value in (
            ("fortress_passed", self.fortress_passed),
            ("gate_promoted", self.gate_promoted),
            ("approved_by_policy", self.approved_by_policy),
        ):
            if type(value) is not bool:
                raise ValueError(f"Promotion attempt {label} must be a boolean")
        if self.council_verdict not in VERDICTS:
            raise ValueError(f"Promotion attempt council verdict must be one of {VERDICTS}")
        if type(self.gate_reasons) is not tuple or not all(
            type(reason) is str for reason in self.gate_reasons
        ):
            raise ValueError("Promotion attempt gate reasons must be a tuple of strings")
        if self.decision not in ("PROMOTED", "REJECTED"):
            raise ValueError("Promotion attempt decision must be PROMOTED or REJECTED")
        if self.gate_promoted != (self.decision == "PROMOTED"):
            raise ValueError("Promotion attempt gate outcome must match its decision")
        if self.approved_by_policy != self.gate_promoted:
            raise ValueError("Promotion attempt approval must match its gate outcome")
        if type(self.evidence_ref) is not str or not _EVIDENCE_REF.fullmatch(self.evidence_ref):
            raise ValueError("Promotion attempt evidence ref is malformed")
        if self.status_after not in _STATUSES:
            raise ValueError(f"Promotion attempt status must be one of {_STATUSES}")
        if self.gate_promoted and self.status_after != PAPER_ACTIVE:
            raise ValueError("A promoted attempt must leave the strategy paper-active")
        if type(self.attempted_at) is not str or not self.attempted_at:
            raise ValueError("Promotion attempt timestamp is required")
        try:
            parsed = datetime.fromisoformat(self.attempted_at)
        except ValueError as exc:
            raise ValueError("Promotion attempt timestamp must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("Promotion attempt timestamp must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "candidate_id": self.candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "fortress_hypothesis_id": self.fortress_hypothesis_id,
            "fortress_verdict_ref": self.fortress_verdict_ref,
            "fortress_passed": self.fortress_passed,
            "council_name": self.council_name,
            "council_verdict": self.council_verdict,
            "council_proposal_id": self.council_proposal_id,
            "gate_promoted": self.gate_promoted,
            "gate_reasons": list(self.gate_reasons),
            "decision": self.decision,
            "approved_by_policy": self.approved_by_policy,
            "evidence_ref": self.evidence_ref,
            "status_after": self.status_after,
            "attempted_at": self.attempted_at,
        }

    @classmethod
    def from_dict(cls, data: object) -> "PromotionAttempt":
        if type(data) is not dict:
            raise ValueError("Promotion attempt record must be a JSON object")
        expected = {
            "strategy_id",
            "candidate_id",
            "hypothesis_id",
            "fortress_hypothesis_id",
            "fortress_verdict_ref",
            "fortress_passed",
            "council_name",
            "council_verdict",
            "council_proposal_id",
            "gate_promoted",
            "gate_reasons",
            "decision",
            "approved_by_policy",
            "evidence_ref",
            "status_after",
            "attempted_at",
        }
        if set(data) != expected:
            raise ValueError("Promotion attempt record shape mismatch")
        reasons = data["gate_reasons"]
        if type(reasons) is not list or not all(type(reason) is str for reason in reasons):
            raise ValueError("Promotion attempt gate reasons must be a list of strings")
        return cls(
            strategy_id=data["strategy_id"],
            candidate_id=data["candidate_id"],
            hypothesis_id=data["hypothesis_id"],
            fortress_hypothesis_id=data["fortress_hypothesis_id"],
            fortress_verdict_ref=data["fortress_verdict_ref"],
            fortress_passed=data["fortress_passed"],
            council_name=data["council_name"],
            council_verdict=data["council_verdict"],
            council_proposal_id=data["council_proposal_id"],
            gate_promoted=data["gate_promoted"],
            gate_reasons=tuple(reasons),
            decision=data["decision"],
            approved_by_policy=data["approved_by_policy"],
            evidence_ref=data["evidence_ref"],
            status_after=data["status_after"],
            attempted_at=data["attempted_at"],
        )


def _encode_attempt(value: PromotionAttempt) -> bytes:
    return json.dumps(
        value.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _attempt_ref(raw: bytes) -> str:
    return "promotion-attempt:" + sha256(raw).hexdigest()


class PromotionRegistry:
    """Audited store of promotion attempts and per-strategy status.

    Attempts are immutable and audit-chained, following the evidence
    store's patterns: canonical JSON bodies, content-hash references,
    capacity bounds, and full integrity re-verification on open. A
    strategy's current status is derived from its recorded attempts --
    ``PAPER_ACTIVE`` exactly when a promoted attempt exists -- so the
    status can never drift from the audited evidence.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connection()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS audit_events "
                "(event_key TEXT PRIMARY KEY,event_json TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS promotion_attempts "
                "(ref TEXT PRIMARY KEY,body BLOB NOT NULL)"
            )
            AuditChain(db).initialize()
            self._verified(db)

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _event(ref: str) -> dict[str, str]:
        return {"action": "PROMOTION_ATTEMPT_RECORDED", "ref": ref}

    def _verified(self, db: sqlite3.Connection) -> dict[str, PromotionAttempt]:
        count, total, largest = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0),COALESCE(MAX(LENGTH(body)),0) "
            "FROM promotion_attempts"
        ).fetchone()
        events, maximum = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        if (
            count > _MAX_RECORDS
            or total > _MAX_TOTAL_BYTES
            or largest > _MAX_RECORD_BYTES
            or events != count
            or maximum > 256
        ):
            raise ValueError("Promotion registry integrity or capacity mismatch")
        if AuditChain(db).require_valid().legacy_event_count:
            raise ValueError("Promotion registry cannot baseline prior audit data")

        attempts: dict[str, PromotionAttempt] = {}
        for ref, raw in db.execute("SELECT ref,body FROM promotion_attempts"):
            if not isinstance(raw, bytes):
                raise ValueError("Promotion registry integrity body type mismatch")
            try:
                value = PromotionAttempt.from_dict(json.loads(raw))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError("Promotion registry integrity model mismatch") from exc
            event = db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (ref,)
            ).fetchone()
            if (
                _encode_attempt(value) != raw
                or _attempt_ref(raw) != ref
                or event is None
                or json.loads(event[0]) != self._event(ref)
            ):
                raise ValueError("Promotion registry integrity binding mismatch")
            attempts[ref] = value
        return attempts

    def record_attempt(self, value: PromotionAttempt) -> str:
        if type(value) is not PromotionAttempt:
            raise ValueError("Typed promotion attempt required")
        raw = _encode_attempt(value)
        if len(raw) > _MAX_RECORD_BYTES:
            raise ValueError("Promotion attempt exceeds record bound")
        ref = _attempt_ref(raw)
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            attempts = self._verified(db)
            if ref in attempts:
                return ref
            total = db.execute(
                "SELECT COALESCE(SUM(LENGTH(body)),0) FROM promotion_attempts"
            ).fetchone()[0]
            if len(attempts) >= _MAX_RECORDS or total + len(raw) > _MAX_TOTAL_BYTES:
                raise ValueError("Promotion registry capacity exhausted")
            db.execute("INSERT INTO promotion_attempts VALUES (?,?)", (ref, raw))
            AuditChain(db).append(ref, self._event(ref))
            self._verified(db)
        return ref

    def get_attempt(self, ref: str) -> PromotionAttempt:
        if type(ref) is not str or not _ATTEMPT_REF.fullmatch(ref):
            raise ValueError("Canonical promotion attempt reference required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            value = self._verified(db).get(ref)
        if value is None:
            raise ValueError("Unknown promotion attempt reference")
        return value

    def list_refs(self) -> tuple[str, ...]:
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            return tuple(sorted(self._verified(db)))

    def attempts_for(self, strategy_id: str) -> tuple[PromotionAttempt, ...]:
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            attempts = self._verified(db)
        ordered = sorted(attempts.items(), key=lambda item: (item[1].attempted_at, item[0]))
        return tuple(attempt for _, attempt in ordered if attempt.strategy_id == strategy_id)

    def status_for(self, strategy_id: str) -> str:
        if any(attempt.gate_promoted for attempt in self.attempts_for(strategy_id)):
            return PAPER_ACTIVE
        return NOT_PROMOTED

    def find_attempt(
        self,
        *,
        strategy_id: str,
        hypothesis_id: str,
        fortress_hypothesis_id: str | None,
        fortress_verdict_ref: str | None,
        council_name: str,
        council_verdict: str,
        council_proposal_id: str,
    ) -> PromotionAttempt | None:
        """Return the recorded attempt for this evidence, if one exists.

        This is the idempotency lookup: an attempt is the same attempt
        when the strategy, the hypothesis binding, the fortress verdict
        reference and the council outcome all match a recorded one.
        """
        for attempt in self.attempts_for(strategy_id):
            if (
                attempt.hypothesis_id == hypothesis_id
                and attempt.fortress_hypothesis_id == fortress_hypothesis_id
                and attempt.fortress_verdict_ref == fortress_verdict_ref
                and attempt.council_name == council_name
                and attempt.council_verdict == council_verdict
                and attempt.council_proposal_id == council_proposal_id
            ):
                return attempt
        return None


@dataclass(frozen=True)
class PromotionOutcome:
    """The result of one :func:`request_promotion` call."""

    promoted: bool
    status: str
    decision_ref: str
    gate_decision: PromotionGateDecision
    reasons: tuple[str, ...]

    @property
    def evidence_ref(self) -> str:
        """Alias for ``decision_ref``: the evidence store reference."""
        return self.decision_ref


def request_promotion(
    registry: PromotionRegistry,
    evidence_store: PromotionEvidenceStore,
    request: PromotionRequest,
    *,
    now: datetime,
) -> PromotionOutcome:
    """Run the promotion gate for ``request`` and record the outcome.

    The gate itself is :func:`evaluate_promotion`; this consumer only
    derives the fortress verdict reference, records the gate's decision
    as a :class:`PromotionDecision` in the evidence store, and records
    the attempt in the registry. The registry status flips to
    ``PAPER_ACTIVE`` only when the gate promoted in this call (or the
    strategy was already paper-active).
    """
    if type(request) is not PromotionRequest:
        raise ValueError("Typed promotion request required")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Promotion requests require a timezone-aware timestamp")

    verdict_ref = (
        fortress_verdict_ref(request.fortress_verdict)
        if request.fortress_verdict is not None
        else None
    )
    gate = evaluate_promotion(request)
    council = gate.council_decision
    council_name = council.council if council is not None else STRATEGY_COUNCIL
    council_proposal_id = council.proposal_id if council is not None else request.strategy_id
    council_verdict = gate.council_verdict or ""

    existing = registry.find_attempt(
        strategy_id=request.strategy_id,
        hypothesis_id=request.hypothesis_id,
        fortress_hypothesis_id=request.fortress_hypothesis_id,
        fortress_verdict_ref=verdict_ref,
        council_name=council_name,
        council_verdict=council_verdict,
        council_proposal_id=council_proposal_id,
    )
    if existing is not None:
        return PromotionOutcome(
            promoted=existing.gate_promoted,
            status=registry.status_for(request.strategy_id),
            decision_ref=existing.evidence_ref,
            gate_decision=gate,
            reasons=gate.reasons,
        )

    if gate.fortress_passed:
        offline_eval = PromotionEvaluation(
            status="PASS", evidence_refs=(verdict_ref,), evaluated_at=now
        )
    elif request.fortress_verdict is not None:
        offline_eval = PromotionEvaluation(
            status="FAIL", evidence_refs=(verdict_ref,), evaluated_at=now
        )
    else:
        offline_eval = PromotionEvaluation(
            status="UNAVAILABLE", reason="no fortress verdict supplied"
        )
    not_gate_eval = PromotionEvaluation(status="NOT_RUN", reason=_NOT_GATE_EVALUATION_REASON)
    decision = PromotionDecision(
        candidate_id=request.strategy_id,
        offline_eval=offline_eval,
        replay_eval=not_gate_eval,
        adversarial_eval=not_gate_eval,
        shadow_eval=not_gate_eval,
        canary_eval=not_gate_eval,
        decision="PROMOTED" if gate.promoted else "REJECTED",
        approved_by_policy=gate.promoted,
        fortress_hypothesis_id=request.fortress_hypothesis_id,
        fortress_verdict_ref=verdict_ref,
        council_name=council_name,
        council_verdict=council_verdict,
        decided_at=now,
        created_at=now,
    )
    evidence_ref = evidence_store.register(decision)

    status_after = (
        PAPER_ACTIVE
        if gate.promoted or registry.status_for(request.strategy_id) == PAPER_ACTIVE
        else NOT_PROMOTED
    )
    registry.record_attempt(
        PromotionAttempt(
            strategy_id=request.strategy_id,
            candidate_id=request.strategy_id,
            hypothesis_id=request.hypothesis_id,
            fortress_hypothesis_id=request.fortress_hypothesis_id,
            fortress_verdict_ref=verdict_ref,
            fortress_passed=gate.fortress_passed,
            council_name=council_name,
            council_verdict=council_verdict,
            council_proposal_id=council_proposal_id,
            gate_promoted=gate.promoted,
            gate_reasons=gate.reasons,
            decision=decision.decision,
            approved_by_policy=decision.approved_by_policy,
            evidence_ref=evidence_ref,
            status_after=status_after,
            attempted_at=now.astimezone(timezone.utc).isoformat(),
        )
    )
    return PromotionOutcome(
        promoted=gate.promoted,
        status=status_after,
        decision_ref=evidence_ref,
        gate_decision=gate,
        reasons=gate.reasons,
    )


__all__ = [
    "NOT_PROMOTED",
    "PAPER_ACTIVE",
    "PromotionAttempt",
    "PromotionOutcome",
    "PromotionRegistry",
    "fortress_verdict_ref",
    "request_promotion",
]

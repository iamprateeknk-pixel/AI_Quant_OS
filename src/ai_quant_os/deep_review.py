"""Bounded advisory escalation; reviewers cannot create risk approvals or order commands."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from threading import Lock
from typing import Callable, Literal, Protocol
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.contracts import _require_aware
from ai_quant_os.decision_gateway import AgentEnvelope, Classification, require_current_decision
from ai_quant_os.world_state import WorldState

ReviewStatus = Literal[
    "COMPLETED", "NOT_CONFIGURED", "WAIT_FOR_DATA", "BUSY", "TIMEOUT", "FAILED", "INVALID"
]


class ReviewCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1.0"]
    world_state_version: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    parent_trace_id: str = Field(min_length=1)
    classification: Classification
    confidence: Decimal = Field(ge=0, le=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    contradictions: tuple[str, ...] = Field(max_length=16)
    rationale: str = Field(min_length=1, max_length=2000)
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def valid_expiry(self) -> "ReviewCandidate":
        if self.expires_at <= self.created_at:
            raise ValueError("Review expiry must follow creation")
        if any(len(item) > 256 for item in self.evidence_refs + self.contradictions):
            raise ValueError("Review references exceed bounds")
        return self


class DeepReviewer(Protocol):
    provider_id: str
    provider_version: str

    async def review(self, snapshot: WorldState, decision: AgentEnvelope) -> object: ...


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: ReviewStatus
    parent_trace_id: str
    instrument_id: str
    classification: Classification | None
    rationale: str | None


class ReviewEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"
    agent_id: Literal["deep-review-dispatcher"] = "deep-review-dispatcher"
    agent_version: Literal["1.0"] = "1.0"
    task_id: str
    world_state_version: str
    input_refs: tuple[str, ...]
    output_type: Literal["RegimeReview"] = "RegimeReview"
    output: ReviewOutput
    confidence: Decimal = Field(ge=0, le=1)
    uncertainty: Decimal = Field(ge=0, le=1)
    evidence_refs: tuple[str, ...]
    contradictions: tuple[str, ...]
    created_at: AwareDatetime
    expires_at: AwareDatetime
    requires_escalation: Literal[True] = True
    model_route: str
    provider_version: str
    trace_id: str


def require_current_review(
    envelope: ReviewEnvelope, *, world_state_version: str, now: datetime
) -> None:
    _require_aware(now)
    if now < envelope.created_at or now >= envelope.expires_at:
        raise ValueError("Review is expired or future-dated")
    if envelope.world_state_version != world_state_version:
        raise ValueError("Review world state changed")


class DeepReviewDispatcher:
    def __init__(
        self,
        *,
        reviewer: DeepReviewer | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 2,
        max_concurrency: int = 4,
    ) -> None:
        if not 0 < timeout_seconds <= 30 or not 1 <= max_concurrency <= 64:
            raise ValueError("Invalid deep review timeout or concurrency")
        if reviewer is not None and any(
            not 1 <= len(value) <= 128
            for value in (reviewer.provider_id, reviewer.provider_version)
        ):
            raise ValueError("Deep reviewer identity and version are required")
        self.reviewer = reviewer
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timeout_seconds = timeout_seconds
        self._max_concurrency = max_concurrency
        self._active = 0
        self._lock = Lock()

    def _current(self, snapshot: WorldState, decision: AgentEnvelope) -> datetime:
        now = self.clock()
        require_current_decision(
            decision, world_state_version=snapshot.world_state_version, now=now
        )
        if not snapshot.as_of <= now < snapshot.valid_until:
            raise ValueError("Review world state is expired or future-dated")
        return now

    async def dispatch(self, snapshot: WorldState, decision: AgentEnvelope) -> ReviewEnvelope:
        now = self._current(snapshot, decision)
        item = next(
            (
                row
                for row in snapshot.instruments
                if row.instrument_id == decision.output.instrument_id
            ),
            None,
        )
        if item is None or not set(decision.evidence_refs).issubset(item.evidence_refs):
            raise ValueError("Review parent evidence or instrument mismatch")
        candidate = None
        status: ReviewStatus = "WAIT_FOR_DATA"
        if decision.output.next_route == "DEEP_REVIEW":
            status = "NOT_CONFIGURED"
            if self.reviewer is not None:
                with self._lock:
                    admitted = self._active < self._max_concurrency
                    if admitted:
                        self._active += 1
                status = "BUSY"
                if admitted:
                    try:
                        remaining = (
                            min(snapshot.valid_until, decision.expires_at) - now
                        ).total_seconds()
                        raw = await asyncio.wait_for(
                            self.reviewer.review(snapshot, decision),
                            timeout=min(self.timeout_seconds, remaining),
                        )
                    except TimeoutError:
                        status = "TIMEOUT"
                    except Exception:
                        status = "FAILED"
                    else:
                        now = self._current(snapshot, decision)
                        try:
                            proposed = ReviewCandidate.model_validate(raw)
                            if (
                                proposed.world_state_version != snapshot.world_state_version
                                or proposed.instrument_id != item.instrument_id
                                or proposed.parent_trace_id != decision.trace_id
                                or proposed.created_at < snapshot.as_of
                                or proposed.created_at > now
                                or proposed.expires_at <= now
                                or not set(proposed.evidence_refs).issubset(item.evidence_refs)
                            ):
                                raise ValueError("Review evidence or identity mismatch")
                            candidate = proposed
                            status = "COMPLETED"
                        except ValueError:
                            status = "INVALID"
                    finally:
                        with self._lock:
                            self._active -= 1
        now = self._current(snapshot, decision)
        confidence = candidate.confidence if candidate else Decimal(0)
        contradictions = candidate.contradictions if candidate else ()
        if candidate and candidate.classification != decision.output.classification:
            contradictions = tuple(
                dict.fromkeys((*contradictions, f"FAST_REGIME:{decision.output.classification}"))
            )
        return ReviewEnvelope(
            task_id=decision.task_id,
            world_state_version=snapshot.world_state_version,
            input_refs=(snapshot.world_state_version, decision.trace_id),
            output=ReviewOutput(
                status=status,
                parent_trace_id=decision.trace_id,
                instrument_id=item.instrument_id,
                classification=candidate.classification if candidate else None,
                rationale=candidate.rationale if candidate else None,
            ),
            confidence=confidence,
            uncertainty=1 - confidence,
            evidence_refs=candidate.evidence_refs if candidate else decision.evidence_refs,
            contradictions=contradictions,
            created_at=now,
            expires_at=min(
                snapshot.valid_until,
                decision.expires_at,
                candidate.expires_at if candidate else decision.expires_at,
            ),
            model_route=self.reviewer.provider_id if self.reviewer else "UNCONFIGURED",
            provider_version=self.reviewer.provider_version if self.reviewer else "NONE",
            trace_id=str(uuid4()),
        )

"""Advisory classification routing; never produces a RiskDecision or OrderCommand."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Literal, Protocol
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.contracts import _require_aware
from ai_quant_os.world_state import WorldState

Classification = Literal["TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"]


class ClassificationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1.0"]
    world_state_version: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    classification: Classification
    confidence: Decimal = Field(ge=0, le=1)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    calibration_ref: str | None = None

    @model_validator(mode="after")
    def expiry_after_creation(self) -> "ClassificationCandidate":
        if self.expires_at <= self.created_at:
            raise ValueError("Candidate expiry must follow creation")
        return self


class DecisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    classification: Classification
    next_route: Literal["DEEP_REVIEW", "WAIT_FOR_DATA"]
    fallback_reasons: tuple[str, ...]
    calibration_ref: str | None = None


class AgentEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"
    agent_id: Literal["fast-decision-gateway"] = "fast-decision-gateway"
    agent_version: Literal["1.0"] = "1.0"
    task_id: str = Field(min_length=1, max_length=128)
    world_state_version: str
    input_refs: tuple[str, ...]
    output_type: Literal["RegimeDecision"] = "RegimeDecision"
    output: DecisionOutput
    confidence: Decimal = Field(ge=0, le=1)
    uncertainty: Decimal = Field(ge=0, le=1)
    evidence_refs: tuple[str, ...]
    contradictions: tuple[str, ...]
    created_at: AwareDatetime
    expires_at: AwareDatetime
    requires_escalation: bool
    model_route: str
    provider_version: str
    trace_id: str


class FastClassifier(Protocol):
    provider_id: str
    provider_version: str

    async def classify(self, snapshot: WorldState, instrument_id: str) -> object: ...


def require_current_decision(
    envelope: AgentEnvelope, *, world_state_version: str, now: datetime
) -> None:
    _require_aware(now)
    if now < envelope.created_at or now >= envelope.expires_at:
        raise ValueError("Decision is expired or future-dated")
    if envelope.world_state_version != world_state_version:
        raise ValueError("Decision world state changed")


class DecisionGateway:
    def __init__(
        self,
        *,
        primary: FastClassifier | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 0.2,
        ttl_seconds: int = 5,
        minimum_confidence: Decimal = Decimal("0.8"),
    ) -> None:
        if not 0 < timeout_seconds <= 30 or not 0 < ttl_seconds <= 60:
            raise ValueError("Invalid decision timeout or TTL")
        if not minimum_confidence.is_finite() or not 0 <= minimum_confidence <= 1:
            raise ValueError("Invalid decision confidence threshold")
        if primary is not None and (not primary.provider_id or not primary.provider_version):
            raise ValueError("Classifier identity and version are required")
        self.primary = primary
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timeout_seconds = timeout_seconds
        self.ttl_seconds = ttl_seconds
        self.minimum_confidence = minimum_confidence

    def _now(self, snapshot: WorldState) -> datetime:
        now = self.clock()
        _require_aware(now)
        if now < snapshot.as_of or now >= snapshot.valid_until:
            raise ValueError("World-state snapshot is expired or future-dated")
        return now

    async def classify(
        self, snapshot: WorldState, *, instrument_id: str, task_id: str
    ) -> AgentEnvelope:
        now = self._now(snapshot)
        if not 1 <= len(task_id) <= 128:
            raise ValueError("Decision task ID must contain 1-128 characters")
        item = next(
            (row for row in snapshot.instruments if row.instrument_id == instrument_id), None
        )
        if item is None:
            raise ValueError("Unknown decision instrument")
        candidate = None
        reasons: list[str] = []
        if self.primary is None:
            reasons.append("PRIMARY_NOT_CONFIGURED")
        elif item.quote_status != "FRESH":
            reasons.append("INSUFFICIENT_EVIDENCE")
        else:
            try:
                raw = await asyncio.wait_for(
                    self.primary.classify(snapshot, instrument_id), timeout=self.timeout_seconds
                )
            except TimeoutError:
                reasons.append("PRIMARY_TIMEOUT")
            except Exception:
                # Provider details may contain credentials, payloads or internal addresses.
                reasons.append("PRIMARY_FAILED")
            else:
                now = self._now(snapshot)
                try:
                    proposed = ClassificationCandidate.model_validate(raw)
                    if (
                        proposed.world_state_version != snapshot.world_state_version
                        or proposed.instrument_id != instrument_id
                        or proposed.created_at < snapshot.as_of
                        or proposed.created_at > now
                        or proposed.expires_at <= now
                        or not set(proposed.evidence_refs).issubset(item.evidence_refs)
                    ):
                        raise ValueError("Classifier evidence or expiry mismatch")
                    candidate = proposed
                except ValueError:
                    reasons.append("PRIMARY_INVALID")
        now = self._now(snapshot)
        classification = item.regime
        confidence = Decimal(0)
        evidence = item.evidence_refs
        route = "QUANTITATIVE"
        version = snapshot.policy_version
        calibration_ref = None
        deadline = min(snapshot.valid_until, now + timedelta(seconds=self.ttl_seconds))
        contradictions: tuple[str, ...] = ()
        if candidate is not None:
            classification = candidate.classification
            confidence = candidate.confidence
            evidence = candidate.evidence_refs
            route = self.primary.provider_id
            version = self.primary.provider_version
            calibration_ref = candidate.calibration_ref
            deadline = min(deadline, candidate.expires_at)
            if confidence < self.minimum_confidence:
                reasons.append("LOW_CONFIDENCE")
            if item.regime != "UNKNOWN" and classification != item.regime:
                contradictions = (f"QUANTITATIVE_REGIME:{item.regime}",)
        # Calibration references are provenance only until a validated registry is installed.
        reasons.append("UNCALIBRATED")
        if classification == "UNKNOWN":
            reasons.append("INSUFFICIENT_EVIDENCE")
        return AgentEnvelope(
            task_id=task_id,
            world_state_version=snapshot.world_state_version,
            input_refs=(snapshot.world_state_version,),
            output=DecisionOutput(
                instrument_id=instrument_id,
                classification=classification,
                next_route="WAIT_FOR_DATA" if classification == "UNKNOWN" else "DEEP_REVIEW",
                fallback_reasons=tuple(dict.fromkeys(reasons)),
                calibration_ref=calibration_ref,
            ),
            confidence=confidence,
            uncertainty=1 - confidence,
            evidence_refs=evidence,
            contradictions=contradictions,
            created_at=now,
            expires_at=deadline,
            requires_escalation=True,
            model_route=route,
            provider_version=version,
            trace_id=str(uuid4()),
        )

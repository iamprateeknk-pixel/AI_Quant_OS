"""TypeSafe Jev Choice adapter with bounded I/O and local failure controls."""

import asyncio
import json
import os
import re
from dataclasses import asdict
from decimal import Decimal
from threading import Lock
from time import monotonic
from typing import Annotated, Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from ai_quant_os.decision_gateway import Classification, ClassificationCandidate
from ai_quant_os.world_state import WorldState

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
CRITERIA = {
    "TRENDING": "Sustained directional movement in the supplied recent FX features.",
    "RANGING": "Low directional movement and no high volatility in the supplied features.",
    "HIGH_VOLATILITY": "Large dispersion of recent step returns in the supplied FX features.",
    "UNKNOWN": "Missing, stale or insufficient evidence to distinguish the other regimes.",
}
Probability = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: Literal["choice"]
    choice: Classification
    confidence: Probability
    probabilities: dict[str, Probability]

    @model_validator(mode="after")
    def coherent_distribution(self) -> "ChoiceAnswer":
        if set(self.probabilities) != set(CRITERIA):
            raise ValueError("Unexpected Jev choice set")
        if abs(sum(self.probabilities.values()) - 1) > Decimal("0.00001"):
            raise ValueError("Invalid Jev probability sum")
        if self.probabilities[self.choice] != max(self.probabilities.values()):
            raise ValueError("Jev choice does not match highest probability")
        return self


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)


class JevResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    answers: dict[str, ChoiceAnswer]
    usage: TokenUsage


class JevClassifier:
    provider_id = "JEV"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 0.15,
        max_requests: int = 100,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30,
        max_request_bytes: int = 32768,
        max_response_bytes: int = 65536,
        transport: httpx.AsyncBaseTransport | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not api_key or len(api_key) > 4096 or any(char.isspace() for char in api_key):
            raise ValueError("Invalid Jev credential configuration")
        if re.fullmatch(r"jev-\d+\.\d+\.\d+", model) is None:
            raise ValueError("Jev requires a pinned versioned model ID")
        if not 0 < timeout_seconds <= 30 or not 1 <= max_requests <= 100000:
            raise ValueError("Invalid Jev timeout or request budget")
        if not 1 <= failure_threshold <= 100 or not 0 < cooldown_seconds <= 3600:
            raise ValueError("Invalid Jev circuit policy")
        if not 1 <= max_request_bytes <= 1000000 or not 1 <= max_response_bytes <= 1000000:
            raise ValueError("Invalid Jev payload limits")
        self.provider_version = model
        self._api_key = SecretStr(api_key)
        self._timeout = timeout_seconds
        self._transport = transport
        self._clock = monotonic_clock
        self._max_requests = max_requests
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._lock = Lock()
        self._requests = self._failures = self._consecutive = 0
        self._open_until = 0.0
        self._probe = False
        self._generation = 0
        self._input_tokens = self._output_tokens = self._usage_reports = 0
        self._provider_confidence: Decimal | None = None

    def status(self) -> dict[str, object]:
        with self._lock:
            state = (
                "OPEN"
                if self._clock() < self._open_until
                else "HALF_OPEN"
                if self._open_until
                else "CLOSED"
            )
            return {
                "provider_id": self.provider_id,
                "provider_version": self.provider_version,
                "request_count": self._requests,
                "failure_count": self._failures,
                "unreported_usage_count": self._requests - self._usage_reports,
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "circuit_state": state,
                "budget_exhausted": self._requests >= self._max_requests,
                "last_provider_confidence": str(self._provider_confidence)
                if self._provider_confidence is not None
                else None,
            }

    def _reserve(self) -> int:
        with self._lock:
            if self._requests >= self._max_requests:
                raise ValueError("Jev request budget exhausted")
            if self._clock() < self._open_until or self._probe:
                raise ValueError("Jev circuit unavailable")
            if self._open_until:
                self._probe = True
            self._requests += 1
            return self._generation

    def _failed(self, generation: int) -> None:
        with self._lock:
            self._failures += 1
            if generation == self._generation:
                self._consecutive += 1
                self._probe = False
                if self._consecutive >= self._threshold:
                    self._open_until = self._clock() + self._cooldown
                    self._generation += 1

    def _record_usage(self, result: JevResponse) -> None:
        with self._lock:
            self._input_tokens += result.usage.input_tokens
            self._output_tokens += result.usage.output_tokens
            self._usage_reports += 1

    def _succeeded(self, generation: int, result: JevResponse) -> None:
        with self._lock:
            self._provider_confidence = result.answers["regime"].confidence
            if generation == self._generation:
                self._consecutive = 0
                self._open_until = 0.0
                self._probe = False

    async def classify(self, snapshot: WorldState, instrument_id: str) -> ClassificationCandidate:
        item = next(
            (row for row in snapshot.instruments if row.instrument_id == instrument_id), None
        )
        if item is None:
            raise ValueError("Unknown Jev instrument")
        # Compile only market evidence; exclude baseline label, account/order data and free text.
        evidence = asdict(item)
        evidence.pop("regime")
        body = {
            "model": self.provider_version,
            "state": {
                "world_state_version": snapshot.world_state_version,
                "as_of": snapshot.as_of.isoformat(),
                "valid_until": snapshot.valid_until.isoformat(),
                "instrument": evidence,
            },
            "questions": {
                "regime": {
                    "type": "choice",
                    "instructions": "Classify the current FX regime using only the supplied paper market evidence. Return UNKNOWN when evidence is inadequate.",
                    "criteria": CRITERIA,
                }
            },
        }
        raw = json.dumps(body, default=str, separators=(",", ":")).encode()
        if len(raw) > self._max_request_bytes:
            raise ValueError("Jev request exceeds payload limit")
        generation = self._reserve()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST",
                    ENDPOINT,
                    content=raw,
                    headers={
                        "Authorization": "Bearer " + self._api_key.get_secret_value(),
                        "Content-Type": "application/json",
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise ValueError("Jev request failed")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError("Unsupported Jev response encoding")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(chunks) + len(chunk) > self._max_response_bytes:
                            raise ValueError("Jev response exceeds payload limit")
                        chunks.extend(chunk)
            result = JevResponse.model_validate_json(bytes(chunks))
            self._record_usage(result)
            if result.model != self.provider_version or set(result.answers) != {"regime"}:
                raise ValueError("Jev model or question identity changed")
            answer = result.answers["regime"]
            candidate = ClassificationCandidate(
                schema_version="1.0",
                world_state_version=snapshot.world_state_version,
                instrument_id=instrument_id,
                classification=answer.choice,
                confidence=answer.probabilities[answer.choice],
                created_at=snapshot.as_of,
                expires_at=snapshot.valid_until,
                evidence_refs=item.evidence_refs,
                calibration_ref=None,
            )
        except asyncio.CancelledError:
            self._failed(generation)
            raise
        except Exception:
            self._failed(generation)
            raise ValueError("Jev provider unavailable or invalid") from None
        self._succeeded(generation, result)
        return candidate


def jev_from_environment() -> JevClassifier | None:
    enabled = os.getenv("AI_QUANT_JEV_ENABLED", "false").lower()
    if enabled == "false":
        return None
    if enabled != "true":
        raise ValueError("AI_QUANT_JEV_ENABLED must be true or false")
    key = os.getenv("AI_QUANT_JEV_API_KEY")
    model = os.getenv("AI_QUANT_JEV_MODEL")
    if not key or not model:
        raise ValueError("Enabled Jev requires server credential and pinned model configuration")
    return JevClassifier(api_key=key, model=model)

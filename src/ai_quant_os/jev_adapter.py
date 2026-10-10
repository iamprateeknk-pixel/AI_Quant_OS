"""TypeSafe Jev Choice adapter with bounded I/O and local failure controls."""

import asyncio
import json
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Annotated, Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from ai_quant_os.decision_gateway import Classification, ClassificationCandidate
from ai_quant_os.jev_spend_policy import JEV_ACCOUNTING, JEV_INPUT_CEILING, JevInputSpendPolicy
from ai_quant_os.provider_call_admission import ProviderOwner, provider_owner
from ai_quant_os.provider_spend_store import ProviderSpendStore
from ai_quant_os.world_state import WorldState

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
CRITERIA = {
    "TRENDING": "Sustained directional movement in the supplied recent FX features.",
    "RANGING": "Low directional movement and no high volatility in the supplied features.",
    "HIGH_VOLATILITY": "Large dispersion of recent step returns in the supplied FX features.",
    "UNKNOWN": "Missing, stale or insufficient evidence to distinguish the other regimes.",
}
Probability = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]


def _unique_response_fields(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("Duplicate Jev response field")
        fields[key] = value
    return fields


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
    input_tokens: int = Field(ge=0, le=10**9, strict=True)
    output_tokens: int = Field(ge=0, le=10**9, strict=True)


class JevUsageEnvelope(BaseModel):
    """Read bounded native usage independently of answer acceptance."""

    model_config = ConfigDict(extra="ignore")
    model: str = Field(min_length=1, max_length=64)
    usage: TokenUsage


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
        spend_store: ProviderSpendStore | None = None,
        spend_owner: ProviderOwner | None = None,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
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
        if (spend_store is None) != (spend_owner is None) or (
            spend_owner is not None and type(spend_owner) is not ProviderOwner
        ):
            raise ValueError("Jev spending ledger requires a declared typed owner")
        self._spend_store, self.spend_owner, self._utc_clock = spend_store, spend_owner, utc_clock
        if spend_store is not None:
            retained = spend_store.status(utc_clock())
            if retained["model"] != model or retained["accounting"] != JEV_ACCOUNTING:
                raise ValueError("Jev spending model or accounting mismatch")
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
            state = {
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
        return {**state, "spending": self.spending_status()}

    def spending_status(self) -> dict[str, object]:
        if self._spend_store is None:
            return {
                "state": "NOT_CONFIGURED",
                "input_token_budget_enforced": False,
                "total_token_budget_enforced": False,
                "declared_rate_budget_enforced": False,
                "invoice_verified": False,
            }
        try:
            retained = self._spend_store.status(self._utc_clock())
            owner = next(
                row for row in retained["agents"] if row["agent_id"] == self.spend_owner.agent_id
            )
            return {
                "state": retained["state"],
                "accounting": retained["accounting"],
                "input_token_budget_enforced": True,
                "total_token_budget_enforced": False,
                "declared_rate_budget_enforced": True,
                "invoice_verified": False,
                "input_reservation_ceiling": JEV_INPUT_CEILING,
                "owner": f"{self.spend_owner.agent_id}@{self.spend_owner.agent_version}",
                "policy_sha256": retained["policy_sha256"],
                "head_hash": retained["head_hash"],
                "day": retained["day"],
                "month": retained["month"],
                "counters": owner,
            }
        except Exception:
            return {"state": "UNAVAILABLE", "invoice_verified": False}

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

    def _record_usage(self, usage: TokenUsage) -> None:
        with self._lock:
            self._input_tokens += usage.input_tokens
            self._output_tokens += usage.output_tokens
            self._usage_reports += 1

    def _succeeded(self, generation: int, result: JevResponse) -> None:
        with self._lock:
            self._provider_confidence = result.answers["regime"].confidence
            if generation == self._generation:
                self._consecutive = 0
                self._open_until = 0.0
                self._probe = False

    async def classify(self, snapshot: WorldState, instrument_id: str) -> ClassificationCandidate:
        return await self.classify_with_admission(snapshot, instrument_id, lambda: None)

    def _check_admission(self, snapshot: WorldState, guard: Callable[[], None]) -> None:
        guard()
        if (
            self._spend_store is not None
            and not snapshot.as_of <= self._utc_clock() < snapshot.valid_until
        ):
            raise ValueError("Jev source unavailable or expired")

    async def classify_with_admission(
        self, snapshot: WorldState, instrument_id: str, guard: Callable[[], None]
    ) -> ClassificationCandidate:
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
        self._check_admission(snapshot, guard)
        generation = self._reserve()
        try:
            reservation = None
            if self._spend_store is not None:
                retained = await asyncio.to_thread(self._spend_store.status, self._utc_clock())
                if retained["state"] != "ENFORCED":
                    raise PermissionError("Jev spending unavailable")
                self._check_admission(snapshot, guard)
                reservation = await asyncio.to_thread(
                    self._spend_store.reserve,
                    agent_id=self.spend_owner.agent_id,
                    agent_version=self.spend_owner.agent_version,
                    model=self.provider_version,
                    request_sha256=sha256(raw).hexdigest(),
                    input_tokens=JEV_INPUT_CEILING,
                    max_output_tokens=0,
                    now=self._utc_clock(),
                )
            self._check_admission(snapshot, guard)
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
            # Ambiguous native usage cannot release an original worst-case hold.
            json.loads(bytes(chunks), object_pairs_hook=_unique_response_fields)
            envelope = JevUsageEnvelope.model_validate_json(bytes(chunks))
            self._record_usage(envelope.usage)
            if reservation is not None and envelope.model == self.provider_version:
                await asyncio.to_thread(
                    self._spend_store.settle,
                    reservation,
                    input_tokens=envelope.usage.input_tokens,
                    output_tokens=envelope.usage.output_tokens,
                    now=self._utc_clock(),
                )
            self._check_admission(snapshot, guard)
            result = JevResponse.model_validate_json(bytes(chunks))
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
    if model != "jev-1.13.0":
        raise ValueError("Enabled Jev requires the reviewed pinned model contract")
    path = os.getenv("AI_QUANT_JEV_SPEND_PATH")
    if not path:
        raise ValueError("Enabled Jev requires persistent spending admission")
    owner = provider_owner(None, "AI_QUANT_CLASSIFY_OWNER")
    if owner is None:
        raise ValueError("Configured Jev spending requires a declared classification owner")
    policy = None
    policy_path = os.getenv("AI_QUANT_JEV_SPEND_POLICY")
    if policy_path:
        with Path(policy_path).open("rb") as source:
            policy = JevInputSpendPolicy.decode(source.read(65537))
    return JevClassifier(
        api_key=key,
        model=model,
        spend_store=ProviderSpendStore(Path(path), policy),
        spend_owner=owner,
    )

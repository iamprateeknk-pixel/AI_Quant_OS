"""Server-declared role ownership and durable call admission for legacy model routes."""

import asyncio
from contextlib import AbstractContextManager, nullcontext
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable

from ai_quant_os.agent_call_budgets import AgentCallBudget
from ai_quant_os.agent_permissions import ROLE_CEILINGS
from ai_quant_os.agent_registry import AgentRegistry, ToolInvocation
from ai_quant_os.contracts import _require_aware
from ai_quant_os.decision_gateway import AgentEnvelope, FastClassifier, require_current_decision
from ai_quant_os.deep_review import DeepReviewer
from ai_quant_os.world_state import WorldState, fingerprint


@dataclass(frozen=True)
class ProviderOwner:
    agent_id: str
    agent_version: str

    def __post_init__(self):
        if (
            type(self.agent_id) is not str
            or self.agent_id not in ROLE_CEILINGS
            or "MODEL_REVIEW" not in ROLE_CEILINGS[self.agent_id]
            or type(self.agent_version) is not str
            or len(self.agent_version) > 32
            or not re.fullmatch(
                r"(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}", self.agent_version
            )
        ):
            raise ValueError("Invalid declared model-call owner")


def provider_owner(declared: ProviderOwner | None, variable: str) -> ProviderOwner | None:
    if declared is not None:
        if type(declared) is not ProviderOwner:
            raise ValueError("Typed provider owner required")
        return declared
    raw = os.getenv(variable)
    if not raw:
        return None
    if len(raw) > 128 or raw.count("@") != 1:
        raise ValueError("Provider owner must be agent@version")
    return ProviderOwner(*raw.split("@"))


class ProviderCallAdmission:
    def __init__(
        self,
        registry: AgentRegistry,
        budget: AgentCallBudget,
        owner: ProviderOwner | None,
        *,
        clock: Callable[[], datetime],
    ):
        self._registry, self._budget, self._owner, self._clock = registry, budget, owner, clock
        if owner is not None:
            registry.get(owner.agent_id, owner.agent_version)

    def check(
        self, snapshot: WorldState, instrument: str, decision: AgentEnvelope | None = None
    ) -> None:
        now = self._clock()
        _require_aware(now)
        if (
            type(snapshot) is not WorldState
            or not snapshot.as_of <= now < snapshot.valid_until
            or instrument not in {row.instrument_id for row in snapshot.instruments}
        ):
            raise ValueError("Provider source is unavailable or expired")
        if decision is not None:
            require_current_decision(
                decision, world_state_version=snapshot.world_state_version, now=now
            )
            if decision.output.instrument_id != instrument:
                raise ValueError("Provider decision source mismatch")
        if self._owner is not None:
            self._registry.require_tool(
                self._owner.agent_id, self._owner.agent_version, "MODEL_REVIEW", scope="PAPER"
            )

    async def reserve(
        self, provider, snapshot: WorldState, instrument: str, decision: AgentEnvelope | None = None
    ) -> None:
        self.check(snapshot, instrument, decision)
        if self._owner is None:
            state = await asyncio.to_thread(self._budget.status)
            if state["state"] != "DISABLED":
                raise PermissionError("Declared provider owner required")
        else:
            if any(
                type(value) is not str or not 1 <= len(value) <= 128
                for value in (provider.provider_id, provider.provider_version)
            ):
                raise ValueError("Provider identity is invalid")
            invocation = ToolInvocation(
                self._owner.agent_id,
                self._owner.agent_version,
                "MODEL_REVIEW",
                "PAPER",
                {
                    "purpose": "DEEP_REVIEW" if decision else "CLASSIFICATION",
                    "provider_id": provider.provider_id,
                    "provider_version": provider.provider_version,
                    "instrument_id": instrument,
                    "world_state_sha256": fingerprint(asdict(snapshot)),
                    "decision": decision.model_dump(mode="json") if decision else None,
                },
                task_id=decision.task_id if decision else None,
            )
            await asyncio.to_thread(self._budget.reserve, invocation)
        self.check(snapshot, instrument, decision)

    def status(self) -> dict[str, object]:
        state = self._budget.status()["state"]
        return {
            "state": "BUDGET_NOT_CONFIGURED"
            if state == "DISABLED"
            else "UNAVAILABLE"
            if state == "UNAVAILABLE"
            else "OWNER_REQUIRED"
            if self._owner is None
            else "ENFORCED",
            "owner": f"{self._owner.agent_id}@{self._owner.agent_version}" if self._owner else None,
            "token_budget_enforced": False,
            "monetary_budget_enforced": False,
        }

    def require_owner(self, owner: ProviderOwner) -> None:
        if owner != self._owner:
            raise ValueError("Provider spending owner differs from route owner")


class AdmittedClassifier:
    def __init__(
        self,
        provider: FastClassifier,
        admission: ProviderCallAdmission,
        *,
        span: Callable[[], AbstractContextManager] = nullcontext,
    ):
        self._provider, self._admission = provider, admission
        self._span = span
        self.provider_id, self.provider_version = provider.provider_id, provider.provider_version
        spend_owner = getattr(provider, "spend_owner", None)
        if spend_owner is not None:
            admission.require_owner(spend_owner)

    async def classify(self, snapshot: WorldState, instrument_id: str) -> object:
        await self._admission.reserve(self._provider, snapshot, instrument_id)
        with self._span():
            guarded = getattr(self._provider, "classify_with_admission", None)
            result = (
                await guarded(
                    snapshot, instrument_id, lambda: self._admission.check(snapshot, instrument_id)
                )
                if callable(guarded)
                else await self._provider.classify(snapshot, instrument_id)
            )
            self._admission.check(snapshot, instrument_id)
        return result


class AdmittedReviewer:
    def __init__(
        self,
        provider: DeepReviewer,
        admission: ProviderCallAdmission,
        *,
        span: Callable[[], AbstractContextManager] = nullcontext,
    ):
        self._provider, self._admission = provider, admission
        self._span = span
        self.provider_id, self.provider_version = provider.provider_id, provider.provider_version
        spend_owner = getattr(provider, "spend_owner", None)
        if spend_owner is not None:
            admission.require_owner(spend_owner)

    async def review(self, snapshot: WorldState, decision: AgentEnvelope) -> object:
        instrument = decision.output.instrument_id
        await self._admission.reserve(self._provider, snapshot, instrument, decision)
        with self._span():
            guarded_review = getattr(self._provider, "review_with_admission", None)
            if callable(guarded_review):
                result = await guarded_review(
                    snapshot,
                    decision,
                    lambda: self._admission.check(snapshot, instrument, decision),
                )
            else:
                result = await self._provider.review(snapshot, decision)
            self._admission.check(snapshot, instrument, decision)
        return result

"""Agent model routing through the AI Cost Governor (V3.2 section 15).

``cost_governor`` owns the routing policy and budget hierarchy, and
``mcp_costs`` adapts it to MCP tool calls — but the agent execution
path itself had no binding: every registered agent implementation is
deterministic (its envelope declares ``DETERMINISTIC_ONLY``), so there
was no layer that decides, per agent task, whether LLM-backed reasoning
may run, at which tier, under which budget, and what happens when it
may not. This module is that binding layer.

Honesty rules, enforced structurally:

- **Providers are disabled by default.** A provider exists only after
  an explicit ``register_provider`` call supplying an invoke callable;
  no provider is ever constructed from ambient environment state, and
  with none registered every LLM-kind task degrades to the
  deterministic path with fallback reason ``PROVIDER_UNAVAILABLE``.
- **The deterministic path is the fallback of last resort, never a
  silent swap.** Every degradation records why: budget DEFER/DENY from
  the governor, a token storm on the agent, a missing provider for the
  admitted tier, or a provider failure at call time.
- **Safety work keeps the governor's guarantee.** Criticality is
  derived from the manifest squad (Safety squad tasks are ``incident``
  work); the governor admits those under the emergency reserve and
  never denies them for budget reasons — the router inherits that
  behaviour instead of re-deciding it.
"""

import time
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, Iterable, Literal

from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.cost_governor import (
    BudgetHierarchy,
    ModelTier,
    TaskRequest,
    TokenStormDetector,
    route_model,
)

#: Default criticality per manifest squad. Safety-squad work (A18,
#: incident classification) is incident work; Reliability (A16) and Data
#: (A07) feed safety decisions, so their work is critical; Validation
#: and Portfolio judgements and the A01 supervisor's coordination are
#: material; Market/Research/Learning/Evolution work is low-priority —
#: it is what the governor defers first when budgets run out.
SQUAD_CRITICALITY: dict[str, str] = {
    "Safety": "incident",
    "Reliability": "critical",
    "Data": "critical",
    "Validation": "material",
    "Portfolio": "material",
    "Control": "material",
    "Market": "low",
    "Research": "low",
    "Learning": "low",
    "Evolution": "low",
}


@dataclass(frozen=True)
class LlmResult:
    """One completed provider call: its output and its actual token use."""

    output: object
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("Provider token usage cannot be negative")


#: A provider call: given the governed task and the admitted tier,
#: produce a result. Providers are plain callables so tests (and any
#: future real adapter) bind without the router importing an SDK.
ProviderInvoke = Callable[[TaskRequest, ModelTier], Awaitable[LlmResult]]
DeterministicInvoke = Callable[[], Awaitable[object]]


@dataclass(frozen=True)
class _Provider:
    provider_id: str
    tiers: frozenset
    invoke: ProviderInvoke


@dataclass(frozen=True)
class RoutingDecision:
    """The router's verdict for one agent task, before and after fallback."""

    agent_id: str
    agent_version: str
    task_id: str
    squad: str
    criticality: str
    kind: str
    governor_decision: str
    routed_tier: ModelTier
    executed_via: Literal["DETERMINISTIC", "PROVIDER"]
    provider_id: str | None
    fallback_reasons: tuple[str, ...]


class AgentModelRouter:
    """Route agent tasks through the cost governor, with honest fallback.

    The router holds no provider by default. ``execute`` always returns
    a result: the provider's output when a provider is registered for
    the admitted tier, admitted by the governor, and calls back
    successfully — otherwise the deterministic callable's output, with
    the reason recorded on the returned :class:`RoutingDecision`.
    """

    def __init__(
        self,
        *,
        budget: BudgetHierarchy | None = None,
        storm_detector: TokenStormDetector | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._budget = budget if budget is not None else BudgetHierarchy()
        self._storm = storm_detector
        self._clock = clock or time.time
        self._providers: dict[str, _Provider] = {}

    def register_provider(
        self,
        provider_id: str,
        *,
        tiers: Iterable[ModelTier],
        invoke: ProviderInvoke,
    ) -> None:
        """Explicitly enable one provider for the given LLM tiers."""
        tier_set = frozenset(tiers)
        if not provider_id or not tier_set or not callable(invoke):
            raise ValueError("Provider id, tiers and invoke callable are required")
        if ModelTier.DETERMINISTIC in tier_set:
            raise ValueError("Providers cannot serve the DETERMINISTIC tier")
        if provider_id in self._providers:
            raise ValueError("Provider is already registered")
        self._providers[provider_id] = _Provider(provider_id, tier_set, invoke)

    @property
    def enabled_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def _provider_for(self, tier: ModelTier) -> _Provider | None:
        for provider_id in sorted(self._providers):
            provider = self._providers[provider_id]
            if tier in provider.tiers:
                return provider
        return None

    def _task(
        self,
        registry: AgentRegistry,
        agent_id: str,
        version: str,
        *,
        task_id: str,
        kind: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> TaskRequest:
        manifest = registry.get(agent_id, version)
        try:
            criticality = SQUAD_CRITICALITY[manifest.squad]
        except KeyError:
            raise ValueError(f"No routing criticality for squad {manifest.squad!r}") from None
        return TaskRequest(
            task_id=task_id,
            agent_id=agent_id,
            squad=manifest.squad,
            criticality=criticality,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            kind=kind,
        )

    def route(
        self,
        registry: AgentRegistry,
        agent_id: str,
        version: str,
        *,
        task_id: str,
        kind: str = "llm",
        estimated_input_tokens: int = 0,
        estimated_output_tokens: int = 0,
        uncertainty: bool = False,
        disagreement: bool = False,
        verifier_failed: bool = False,
    ) -> RoutingDecision:
        """Decide how one agent task would execute, without executing it."""
        task = self._task(
            registry,
            agent_id,
            version,
            task_id=task_id,
            kind=kind,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
        )
        tier = route_model(
            task,
            uncertainty=uncertainty,
            disagreement=disagreement,
            verifier_failed=verifier_failed,
        )
        if tier is ModelTier.DETERMINISTIC:
            return RoutingDecision(
                agent_id=agent_id,
                agent_version=version,
                task_id=task_id,
                squad=task.squad,
                criticality=task.criticality,
                kind=kind,
                governor_decision="ADMIT",
                routed_tier=tier,
                executed_via="DETERMINISTIC",
                provider_id=None,
                fallback_reasons=(),
            )
        admission = self._budget.admit(task, tier)
        fallbacks: list[str] = []
        provider: _Provider | None = None
        if admission.decision in ("DEFER", "DENY"):
            fallbacks.append(f"BUDGET_{admission.decision}")
        elif self._storm is not None and self._storm.is_storm(agent_id, self._clock()):
            fallbacks.append("TOKEN_STORM")
        else:
            provider = self._provider_for(admission.tier)
            if provider is None:
                fallbacks.append("PROVIDER_UNAVAILABLE")
        return RoutingDecision(
            agent_id=agent_id,
            agent_version=version,
            task_id=task_id,
            squad=task.squad,
            criticality=task.criticality,
            kind=kind,
            governor_decision=admission.decision,
            routed_tier=admission.tier,
            executed_via="PROVIDER" if provider is not None else "DETERMINISTIC",
            provider_id=provider.provider_id if provider is not None else None,
            fallback_reasons=tuple(fallbacks),
        )

    async def execute(
        self,
        registry: AgentRegistry,
        agent_id: str,
        version: str,
        *,
        task_id: str,
        deterministic: DeterministicInvoke,
        kind: str = "llm",
        estimated_input_tokens: int = 0,
        estimated_output_tokens: int = 0,
        uncertainty: bool = False,
        disagreement: bool = False,
        verifier_failed: bool = False,
    ) -> tuple[RoutingDecision, object]:
        """Execute one agent task, honouring the routing decision.

        The provider runs only when the decision says so; any provider
        exception degrades to the deterministic callable with
        ``PROVIDER_FAILED`` appended to the fallback reasons. Actual
        provider token usage is recorded with the governor only when a
        provider call completed.
        """
        if self._storm is not None:
            self._storm.observe(task_id, agent_id, self._clock())
        decision = self.route(
            registry,
            agent_id,
            version,
            task_id=task_id,
            kind=kind,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            uncertainty=uncertainty,
            disagreement=disagreement,
            verifier_failed=verifier_failed,
        )
        if decision.executed_via == "PROVIDER":
            provider = self._providers[decision.provider_id]
            task = self._task(
                registry,
                agent_id,
                version,
                task_id=task_id,
                kind=kind,
                estimated_input_tokens=estimated_input_tokens,
                estimated_output_tokens=estimated_output_tokens,
            )
            try:
                result = await provider.invoke(task, decision.routed_tier)
            except Exception:
                decision = replace(
                    decision,
                    executed_via="DETERMINISTIC",
                    provider_id=None,
                    fallback_reasons=(*decision.fallback_reasons, "PROVIDER_FAILED"),
                )
            else:
                if not isinstance(result, LlmResult):
                    raise ValueError("Provider returned an invalid result")
                self._budget.record_usage(task, result.input_tokens, result.output_tokens)
                return decision, result.output
        return decision, await deterministic()

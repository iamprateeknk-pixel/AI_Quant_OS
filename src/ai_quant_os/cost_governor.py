"""AI Compute & Cost Governor (V3.2 blueprint section 15).

Deterministic, stdlib-only governance for AI spend. The Governor sits in the
AI control plane, never in the capital path: it may throttle, defer or reroute
AI work, but it can never weaken deterministic safety. Two laws from the
blueprint are enforced structurally here:

- Budget exhaustion pauses low-priority research/evolution work first.
  Incident (safety) work draws on a ring-fenced emergency reserve and is
  never denied for budget reasons.
- Do not call an LLM for work a deterministic rule can do: tasks declared
  ``kind="deterministic"`` route to the DETERMINISTIC tier and bypass every
  token budget.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256

_CRITICALITIES = ("low", "material", "critical", "incident")
_KINDS = ("llm", "deterministic")


class ModelTier(str, Enum):
    """Model tiers, cheapest sufficient tier first (section 15 routing table)."""

    DETERMINISTIC = "DETERMINISTIC"
    ROUTINE = "ROUTINE"
    DEEP = "DEEP"
    FRONTIER = "FRONTIER"


#: LLM tiers from highest to lowest; a downgrade moves one step toward ROUTINE.
_TIER_ORDER = (ModelTier.FRONTIER, ModelTier.DEEP, ModelTier.ROUTINE)


def _downgrade(tier: ModelTier) -> ModelTier | None:
    """Return the next cheaper LLM tier, or None when already at ROUTINE."""
    if tier not in _TIER_ORDER:
        return None
    index = _TIER_ORDER.index(tier)
    if index == len(_TIER_ORDER) - 1:
        return None
    return _TIER_ORDER[index + 1]


@dataclass(frozen=True)
class TaskRequest:
    """One unit of AI work asking the Governor for a model route and budget.

    ``criticality`` is one of ``low`` / ``material`` / ``critical`` /
    ``incident``. ``kind`` is ``llm`` (default) or ``deterministic`` for work
    that must be done by rules/code, not by a model.
    """

    task_id: str
    agent_id: str
    squad: str
    criticality: str
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    kind: str = "llm"

    def __post_init__(self) -> None:
        if not self.task_id or not self.agent_id or not self.squad:
            raise ValueError("Task id, agent id and squad are required")
        if self.criticality not in _CRITICALITIES:
            raise ValueError(f"Unknown criticality: {self.criticality!r}")
        if self.kind not in _KINDS:
            raise ValueError(f"Unknown task kind: {self.kind!r}")
        if self.estimated_input_tokens < 0 or self.estimated_output_tokens < 0:
            raise ValueError("Token estimates cannot be negative")

    @property
    def estimated_tokens(self) -> int:
        return self.estimated_input_tokens + self.estimated_output_tokens

    @property
    def is_safety(self) -> bool:
        """Incident work is safety work: it owns the emergency reserve."""
        return self.criticality == "incident"


def route_model(
    task: TaskRequest,
    *,
    uncertainty: bool = False,
    disagreement: bool = False,
    verifier_failed: bool = False,
) -> ModelTier:
    """Select the cheapest model tier that reliably satisfies ``task``.

    Routing policy (section 15 model-routing table):

    - ``kind="deterministic"`` always routes to DETERMINISTIC: no LLM call.
    - ``incident`` and ``critical`` work routes to DEEP, escalating to
      FRONTIER only when an escalation flag is set (``uncertainty``,
      ``disagreement`` between independent agents, or ``verifier_failed``).
    - ``material`` work routes to ROUTINE, escalating to DEEP when any
      escalation flag is set.
    - ``low`` work always routes to ROUTINE. Escalation flags never promote
      low-criticality work: cheap work must not consume deep/frontier
      capacity. A caller that believes the work is material must resubmit
      it at that criticality.
    """
    if task.kind == "deterministic":
        return ModelTier.DETERMINISTIC
    escalated = uncertainty or disagreement or verifier_failed
    if task.criticality in ("incident", "critical"):
        return ModelTier.FRONTIER if escalated else ModelTier.DEEP
    if task.criticality == "material":
        return ModelTier.DEEP if escalated else ModelTier.ROUTINE
    return ModelTier.ROUTINE


@dataclass(frozen=True)
class Admission:
    """Governor verdict for one task: what may run, at which tier, and why."""

    decision: str
    tier: ModelTier
    reason: str

    def __post_init__(self) -> None:
        if self.decision not in ("ADMIT", "DOWNGRADE", "DEFER", "DENY"):
            raise ValueError(f"Unknown admission decision: {self.decision!r}")


class BudgetHierarchy:
    """Daily token budgets: platform, squad, agent and per-task (section 15).

    The emergency reserve is ring-fenced inside the platform budget:
    non-safety tasks may only spend up to
    ``daily_platform_budget_tokens - emergency_reserve_tokens``. Safety
    (incident) tasks skip every budget check — they are downgraded by the
    per-task cap like any other task, but never DEFERred or DENIEd for
    budget reasons, and their usage is still recorded even when it pushes
    recorded spend past the nominal budget.

    Admission rules, checked in order:

    1. DETERMINISTIC tier: ADMIT. Deterministic work spends no LLM budget
       and is never throttled.
    2. Per-task cap: an estimate above ``task_token_cap`` is DOWNGRADEd one
       tier (FRONTIER -> DEEP -> ROUTINE); at ROUTINE it is DENIEd — except
       safety tasks, which are ADMITted at ROUTINE (never denied).
    3. Platform, then squad, then agent budget, projected against recorded
       spend plus this task's estimate: ``low`` tasks are DEFERred;
       other tasks are DOWNGRADEd one tier when a cheaper tier exists and
       DENIEd at ROUTINE.
    4. Otherwise ADMIT at the requested tier.
    """

    def __init__(
        self,
        *,
        daily_platform_budget_tokens: int = 25_000_000,
        squad_budget_tokens: int = 8_000_000,
        agent_budget_tokens: int = 2_000_000,
        task_token_cap: int = 250_000,
        emergency_reserve_tokens: int = 3_000_000,
    ) -> None:
        values = (
            daily_platform_budget_tokens,
            squad_budget_tokens,
            agent_budget_tokens,
            task_token_cap,
            emergency_reserve_tokens,
        )
        if any(value <= 0 for value in values):
            raise ValueError("Budget values must be positive")
        if emergency_reserve_tokens >= daily_platform_budget_tokens:
            raise ValueError("Emergency reserve must be smaller than the platform budget")
        self._platform_budget = daily_platform_budget_tokens
        self._squad_budget = squad_budget_tokens
        self._agent_budget = agent_budget_tokens
        self._task_cap = task_token_cap
        self._emergency_reserve = emergency_reserve_tokens
        self._platform_spent = 0
        self._squad_spent: dict[str, int] = {}
        self._agent_spent: dict[str, int] = {}

    @property
    def platform_spent_tokens(self) -> int:
        return self._platform_spent

    def squad_spent_tokens(self, squad: str) -> int:
        return self._squad_spent.get(squad, 0)

    def agent_spent_tokens(self, agent_id: str) -> int:
        return self._agent_spent.get(agent_id, 0)

    def record_usage(self, task: TaskRequest, input_tokens: int, output_tokens: int) -> None:
        """Record actual token spend against platform, squad and agent."""
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token usage cannot be negative")
        total = input_tokens + output_tokens
        self._platform_spent += total
        self._squad_spent[task.squad] = self.squad_spent_tokens(task.squad) + total
        self._agent_spent[task.agent_id] = self.agent_spent_tokens(task.agent_id) + total

    def _budget_verdict(self, task: TaskRequest, tier: ModelTier, scope: str) -> Admission:
        """Shared verdict when a budget scope is exhausted."""
        if task.criticality == "low":
            return Admission("DEFER", tier, f"{scope} budget exhausted; low-priority task deferred")
        cheaper = _downgrade(tier)
        if cheaper is not None:
            return Admission("DOWNGRADE", cheaper, f"{scope} budget exhausted; routed down a tier")
        return Admission("DENY", tier, f"{scope} budget exhausted")

    def admit(self, task: TaskRequest, tier: ModelTier) -> Admission:
        if tier is ModelTier.DETERMINISTIC:
            return Admission("ADMIT", tier, "deterministic work spends no LLM budget")
        estimate = task.estimated_tokens
        if estimate > self._task_cap:
            cheaper = _downgrade(tier)
            if cheaper is not None:
                return Admission("DOWNGRADE", cheaper, "task estimate exceeds the per-task cap")
            if task.is_safety:
                return Admission(
                    "ADMIT", tier, "per-task cap exceeded, but safety work is never denied"
                )
            return Admission("DENY", tier, "task estimate exceeds the per-task cap")
        if task.is_safety:
            return Admission("ADMIT", tier, "safety task admitted under the emergency reserve")
        non_reserve_limit = self._platform_budget - self._emergency_reserve
        if self._platform_spent + estimate > non_reserve_limit:
            return self._budget_verdict(task, tier, "platform")
        if self.squad_spent_tokens(task.squad) + estimate > self._squad_budget:
            return self._budget_verdict(task, tier, "squad")
        if self.agent_spent_tokens(task.agent_id) + estimate > self._agent_budget:
            return self._budget_verdict(task, tier, "agent")
        return Admission("ADMIT", tier, "within platform, squad, agent and task budgets")


class TokenStormDetector:
    """Sliding-window call-velocity and handoff-loop detector (section 15).

    ``is_storm`` is True for an agent when either:

    - the number of calls inside the sliding window exceeds
      ``max_calls_per_window`` (a retry/call storm), or
    - one ``task_id`` was observed at least ``loop_repeat_threshold`` times
      inside the window (a recursive agent handoff loop).
    """

    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        max_calls_per_window: int = 30,
        loop_repeat_threshold: int = 3,
    ) -> None:
        if window_seconds <= 0 or max_calls_per_window <= 0 or loop_repeat_threshold <= 1:
            raise ValueError("Storm detector window, threshold and loop threshold are invalid")
        self._window = window_seconds
        self._max_calls = max_calls_per_window
        self._loop_threshold = loop_repeat_threshold
        self._calls: dict[str, list[tuple[float, str]]] = {}

    def _recent(self, agent_id: str, now: float) -> list[tuple[float, str]]:
        cutoff = now - self._window
        recent = [call for call in self._calls.get(agent_id, []) if call[0] > cutoff]
        self._calls[agent_id] = recent
        return recent

    def observe(self, task_id: str, agent_id: str, timestamp_seconds: float) -> None:
        recent = self._recent(agent_id, timestamp_seconds)
        recent.append((timestamp_seconds, task_id))
        self._calls[agent_id] = recent

    def is_storm(self, agent_id: str, now: float) -> bool:
        recent = self._recent(agent_id, now)
        if len(recent) > self._max_calls:
            return True
        counts: dict[str, int] = {}
        for _, task_id in recent:
            counts[task_id] = counts.get(task_id, 0) + 1
            if counts[task_id] >= self._loop_threshold:
                return True
        return False


def score_agent(
    *,
    tasks_completed: int,
    tasks_accepted: int,
    total_cost_usd: Decimal,
    quality: Decimal,
) -> Decimal:
    """Cost-to-value score for one agent (section 15 cost/value scorecard).

    ``value = quality * acceptance_rate - cost_per_completed_task`` where
    ``acceptance_rate = tasks_accepted / tasks_completed`` (0 when no task
    completed) and the cost component is the mean USD cost per completed
    task (0 when no task completed). Higher is better; a negative score
    means the agent costs more per task than its accepted quality returns.
    """
    if tasks_completed < 0 or tasks_accepted < 0:
        raise ValueError("Task counts cannot be negative")
    if tasks_accepted > tasks_completed:
        raise ValueError("Accepted tasks cannot exceed completed tasks")
    if total_cost_usd < 0:
        raise ValueError("Cost cannot be negative")
    if quality < 0 or quality > 1:
        raise ValueError("Quality must be between 0 and 1")
    if tasks_completed == 0:
        return Decimal(0)
    acceptance_rate = Decimal(tasks_accepted) / Decimal(tasks_completed)
    cost_per_task = total_cost_usd / Decimal(tasks_completed)
    return quality * acceptance_rate - cost_per_task


@dataclass(frozen=True)
class CostValueScore:
    """One agent's scorecard row; ``value_score`` uses the score_agent formula."""

    agent_id: str
    tasks_completed: int
    tasks_accepted: int
    total_cost_usd: Decimal
    quality: Decimal
    value_score: Decimal

    @classmethod
    def compute(
        cls,
        agent_id: str,
        *,
        tasks_completed: int,
        tasks_accepted: int,
        total_cost_usd: Decimal,
        quality: Decimal,
    ) -> "CostValueScore":
        return cls(
            agent_id=agent_id,
            tasks_completed=tasks_completed,
            tasks_accepted=tasks_accepted,
            total_cost_usd=total_cost_usd,
            quality=quality,
            value_score=score_agent(
                tasks_completed=tasks_completed,
                tasks_accepted=tasks_accepted,
                total_cost_usd=total_cost_usd,
                quality=quality,
            ),
        )


def cache_key(system_instructions: str, evidence_pack_id: str, tool_schema_version: str) -> str:
    """Stable prompt-cache key for a reusable context package (section 15).

    The key is the SHA-256 hex digest of the three components joined by a
    unit separator, so identical system instructions, evidence pack and tool
    schema versions always reuse the same cache entry, and any change to
    any component produces a different key.
    """
    material = "\x1f".join((system_instructions, evidence_pack_id, tool_schema_version))
    return sha256(material.encode("utf-8")).hexdigest()

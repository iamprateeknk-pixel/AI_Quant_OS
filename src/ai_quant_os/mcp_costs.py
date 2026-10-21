"""Cost-governor adapter for MCP tool calls.

Thin adapter enforcing the governor's per-task cap and budget hierarchy
on MCP tool calls that declare a token cost; never alters governor
semantics. Admission decisions, routing and budget accounting all stay
in :mod:`ai_quant_os.cost_governor` -- this module only translates an
MCP tool call into a :class:`TaskRequest`, asks the governor, and turns
a non-ADMIT verdict into a :class:`CostCapRefusal` (a ``PermissionError``,
so the HTTP transport refuses the call with 403 like any other denial).
"""

from ai_quant_os.cost_governor import (
    BudgetHierarchy,
    ModelTier,
    TaskRequest,
    route_model,
)


class CostCapRefusal(PermissionError):
    """An MCP tool call was refused by the cost governor."""


class McpCostGovernor:
    """Apply a :class:`BudgetHierarchy` to MCP tool calls.

    Each call is modelled as a ``material`` LLM task in the
    ``mcp-tools`` squad whose entire estimate is input tokens. The
    governor therefore enforces its per-task cap first and then the
    platform / squad / agent budgets, exactly as for any other task.
    """

    def __init__(self, budget: BudgetHierarchy | None = None) -> None:
        self._budget = budget if budget is not None else BudgetHierarchy()
        self._task_token_cap: int | None = None

    @staticmethod
    def _task(agent_id: str, task_id: str, estimated_tokens: int) -> TaskRequest:
        return TaskRequest(
            task_id=task_id,
            agent_id=agent_id,
            squad="mcp-tools",
            criticality="material",
            estimated_input_tokens=estimated_tokens,
            estimated_output_tokens=0,
            kind="llm",
        )

    def check(self, *, agent_id: str, task_id: str, estimated_tokens: int) -> None:
        """Raise :class:`CostCapRefusal` unless the governor admits the call."""
        task = self._task(agent_id, task_id, estimated_tokens)
        tier = route_model(task)
        admission = self._budget.admit(task, tier)
        if admission.decision != "ADMIT":
            raise CostCapRefusal(f"MCP tool call refused by cost governor: {admission.reason}")

    def record(self, *, agent_id: str, task_id: str, tokens: int) -> None:
        """Record actual token spend for a completed MCP tool call."""
        task = self._task(agent_id, task_id, tokens)
        self._budget.record_usage(task, tokens, 0)

    @property
    def task_token_cap(self) -> int:
        """The budget's per-task token cap, discovered by probing ``admit``.

        The governor exposes no public cap getter, so the cap is found
        empirically: at the ROUTINE tier a non-safety ``material`` task
        whose estimate exceeds the per-task cap is DENIED for the cap
        before any budget scope is consulted. Binary-searching that
        boundary yields the smallest denied estimate, one above the cap.
        The result is cached; probing records no usage.
        """
        if self._task_token_cap is not None:
            return self._task_token_cap

        def is_cap_denied(estimate: int) -> bool:
            probe = TaskRequest(
                task_id="mcp-cost-cap-probe",
                agent_id="cap-probe",
                squad="mcp-tools",
                criticality="material",
                estimated_input_tokens=estimate,
                estimated_output_tokens=0,
                kind="llm",
            )
            admission = self._budget.admit(probe, ModelTier.ROUTINE)
            return admission.decision == "DENY" and "per-task cap" in admission.reason

        low = 0
        high = 1
        while not is_cap_denied(high):
            low = high
            high *= 2
        while high - low > 1:
            mid = (low + high) // 2
            if is_cap_denied(mid):
                high = mid
            else:
                low = mid
        self._task_token_cap = low
        return low


__all__ = [
    "CostCapRefusal",
    "McpCostGovernor",
]

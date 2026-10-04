"""Current task, permission and review admission for inert candidate publication."""

from datetime import datetime
from typing import Callable

from ai_quant_os.agent_context import CompiledContext, require_context_for_agent
from ai_quant_os.agent_registry import AgentRegistry, ToolInvocation
from ai_quant_os.health_watchdog import owned_health_call
from ai_quant_os.strategy_build_store import StrategyBuildStore
from ai_quant_os.strategy_builder_sources import candidate_for_context


class CandidateCreate:
    def __init__(
        self,
        store: StrategyBuildStore,
        registry: AgentRegistry,
        *,
        context: Callable[[str], CompiledContext],
        clock: Callable[[], datetime],
    ):
        self._store, self._registry, self._context, self._clock = store, registry, context, clock

    async def __call__(self, invocation: ToolInvocation) -> dict:
        if (
            (invocation.agent_id, invocation.agent_version, invocation.scope, invocation.tool)
            != ("A09", "0.2.0", "SANDBOX", "CANDIDATE_CREATE")
            or invocation.payload
            or not invocation.context_ref
            or not invocation.task_id
        ):
            raise PermissionError("Unsupported candidate publication authority")
        context = self._context(invocation.context_ref)
        if context.context_ref != invocation.context_ref:
            raise PermissionError("Candidate context identity mismatch")

        def admit():
            require_context_for_agent(
                context,
                registry=self._registry,
                agent_id="A09",
                agent_version="0.2.0",
                task_id=invocation.task_id,
                scope="SANDBOX",
                now=self._clock(),
            )
            self._registry.require_tool("A09", "0.2.0", "CANDIDATE_CREATE", scope="SANDBOX")

        admit()
        candidate = candidate_for_context(context)

        def publish():
            admit()
            return self._store.put_candidate(candidate, now=self._clock())

        saved = await owned_health_call(publish)
        admit()
        active = await owned_health_call(
            lambda: self._store.active(candidate.review.review_ref, now=self._clock())
        )
        admit()
        if active != candidate.review or saved != candidate:
            raise PermissionError("Candidate review changed during publication")
        return {"candidate_ref": saved.candidate_ref, "status": "INERT_NOT_VALIDATED"}

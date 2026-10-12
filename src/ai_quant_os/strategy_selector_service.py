"""Task-bound live check for the current read-only candidate registry snapshot."""

from datetime import datetime
from typing import Callable

from ai_quant_os.agent_context import CompiledContext, require_context_for_agent
from ai_quant_os.agent_registry import AgentRegistry, ToolInvocation
from ai_quant_os.research_candidate_store import CandidateStore
from ai_quant_os.strategy_selector_agent import _observations
from ai_quant_os.strategy_selector_sources import RegistrySnapshot, registry_observation


class StrategyRegistryRead:
    def __init__(
        self,
        store: Callable[[], CandidateStore | None],
        registry: AgentRegistry,
        *,
        context: Callable[[str], CompiledContext],
        clock: Callable[[], datetime],
    ) -> None:
        self._store, self._registry = store, registry
        self._context, self._clock = context, clock

    async def __call__(self, invocation: ToolInvocation) -> dict:
        if (
            (invocation.agent_id, invocation.agent_version, invocation.scope, invocation.tool)
            != ("A12", "0.2.0", "PAPER", "STRATEGY_REGISTRY_READ")
            or not invocation.context_ref
            or not invocation.task_id
        ):
            raise PermissionError("Unsupported strategy registry authority")
        context = self._context(invocation.context_ref)
        if context.context_ref != invocation.context_ref:
            raise PermissionError("Selector context identity mismatch")

        def admit():
            require_context_for_agent(
                context,
                registry=self._registry,
                agent_id="A12",
                agent_version="0.2.0",
                task_id=invocation.task_id,
                scope="PAPER",
                now=self._clock(),
            )
            self._registry.require_tool("A12", "0.2.0", "STRATEGY_REGISTRY_READ", scope="PAPER")

        admit()
        observation, _ = _observations(context)
        if invocation.payload != {"registry_sha256": observation.registry_sha256}:
            raise PermissionError("Selector request differs from admitted registry evidence")
        store = self._store()
        snapshot = RegistrySnapshot(
            available=store is not None,
            candidates=() if store is None else store.list(),
        )
        current = registry_observation(snapshot, now=observation.as_of)
        if current.registry_sha256 != observation.registry_sha256 or len(current.candidates) != len(
            observation.candidates
        ):
            raise ValueError("Strategy registry changed after context compilation")
        admit()
        return {
            "registry_sha256": current.registry_sha256,
            "candidate_count": len(current.candidates),
            "status": "CHECKED_REGISTRY_SNAPSHOT",
        }

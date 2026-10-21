"""Task-bound live check for the current read-only candidate registry snapshot."""

from datetime import datetime
from typing import Callable

from ai_quant_os.agent_context import CompiledContext, require_context_for_agent
from ai_quant_os.agent_registry import AgentRegistry, ToolInvocation
from ai_quant_os.research_candidate_store import CandidateStore
from ai_quant_os.shadow_store import ShadowStore
from ai_quant_os.strategy_selector_agent import _observations, _observations_v3
from ai_quant_os.strategy_selector_sources import (
    RegistrySnapshot,
    registry_observation,
    run_shadow_performance_scan,
)


class StrategyRegistryRead:
    def __init__(
        self,
        store: Callable[[], CandidateStore | None],
        registry: AgentRegistry,
        *,
        context: Callable[[str], CompiledContext],
        clock: Callable[[], datetime],
        shadow_store: Callable[[], ShadowStore | None] | None = None,
    ) -> None:
        self._store, self._registry = store, registry
        self._context, self._clock = context, clock
        self._shadow_store = shadow_store or (lambda: None)

    async def __call__(self, invocation: ToolInvocation) -> dict:
        if (
            (invocation.agent_id, invocation.agent_version, invocation.scope, invocation.tool)
            not in {
                ("A12", "0.2.0", "PAPER", "STRATEGY_REGISTRY_READ"),
                ("A12", "0.3.0", "PAPER", "STRATEGY_REGISTRY_READ"),
            }
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
                agent_version=invocation.agent_version,
                task_id=invocation.task_id,
                scope="PAPER",
                now=self._clock(),
            )
            self._registry.require_tool(
                "A12",
                invocation.agent_version,
                "STRATEGY_REGISTRY_READ",
                scope="PAPER",
            )

        admit()
        if invocation.agent_version == "0.2.0":
            observation, _ = _observations(context)
            if invocation.payload != {"registry_sha256": observation.registry_sha256}:
                raise PermissionError("Selector request differs from admitted registry evidence")
            expected_performance = None
        else:
            observation, _, performance, _ = _observations_v3(context)
            if invocation.payload != {
                "registry_sha256": observation.registry_sha256,
                "performance_sha256": performance.fingerprint,
            }:
                raise PermissionError("Selector request differs from admitted evidence")
            expected_performance = performance.fingerprint
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
        if expected_performance is not None:
            current_performance = await run_shadow_performance_scan(
                self._shadow_store(), now=performance.as_of
            )
            if current_performance.fingerprint != expected_performance:
                raise ValueError("Shadow performance changed after context compilation")
        admit()
        receipt = {
            "registry_sha256": current.registry_sha256,
            "candidate_count": len(current.candidates),
            "status": "CHECKED_REGISTRY_SNAPSHOT",
        }
        if expected_performance is not None:
            receipt["performance_sha256"] = expected_performance
            receipt["status"] = "CHECKED_REGISTRY_AND_SHADOW_SNAPSHOT"
        return receipt

"""Bound parent orchestration of independently admitted, persisted paper child graphs."""

import asyncio
from typing import Awaitable, Callable
import sqlite3

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_registry import AgentRegistry, ToolInvocation
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.graph_ledger import GraphReceipt
from ai_quant_os.supervisor_agent import (
    SpecialistCall,
    child_run_id,
    read_supervisor_context,
    wake_plan,
)


def dispatch_contract(version: str):
    if version == "0.4.0":
        from ai_quant_os.task_supervisor import read_context, wake_plan as task_wake_plan
        from ai_quant_os.supervisor_drift import child_run_id as task_child_id, DriftSpecialistCall

        return read_context, task_wake_plan, task_child_id, DriftSpecialistCall
    if version == "0.3.0":
        from ai_quant_os.supervisor_drift import (
            read_context,
            wake_plan as drift_wake_plan,
            child_run_id as drift_child_id,
            DriftSpecialistCall,
        )

        return read_context, drift_wake_plan, drift_child_id, DriftSpecialistCall
    if version == "0.2.0":
        return read_supervisor_context, wake_plan, child_run_id, SpecialistCall
    raise PermissionError("Unsupported supervisor dispatch version")


class SupervisorDispatch:
    def __init__(
        self,
        registry: AgentRegistry,
        *,
        runtime: Callable[[], GraphRuntime],
        context: Callable[[str], CompiledContext],
        compile_child: Callable[[str, str], Awaitable[CompiledContext]],
        admission: Callable[[], Awaitable[None]],
        observe: Callable[[GraphReceipt], Awaitable[None]] | None = None,
    ):
        self._registry, self._runtime, self._context = registry, runtime, context
        self._compile_child, self._admission = compile_child, admission
        self._observe = observe
        self._locks: dict[str, asyncio.Lock] = {}

    def _parent(self, invocation: ToolInvocation) -> CompiledContext:
        read_context, _, _, _ = dispatch_contract(invocation.agent_version)
        if (invocation.agent_id, invocation.scope, invocation.tool) != (
            "A01",
            "PAPER",
            "TASK_ORCHESTRATE",
        ) or invocation.payload:
            raise PermissionError("Unsupported supervisor dispatch authority")
        if not invocation.context_ref or not invocation.task_id:
            raise PermissionError("Supervisor dispatch requires runtime-bound parent identity")
        parent = self._context(invocation.context_ref)
        if (parent.context_ref, parent.task_id) != (invocation.context_ref, invocation.task_id):
            raise PermissionError("Supervisor parent context binding mismatch")
        self._runtime().require_context(parent)
        if parent.agent_version != invocation.agent_version:
            raise PermissionError("Supervisor parent version mismatch")
        self._registry.require_tool(
            "A01", invocation.agent_version, "TASK_ORCHESTRATE", scope="PAPER"
        )
        read_context(parent)
        return parent

    async def __call__(self, invocation: ToolInvocation) -> dict[str, object]:
        parent = self._parent(invocation)
        if parent.context_ref not in self._locks:
            if len(self._locks) >= 10000:
                raise ValueError("Supervisor dispatch capacity exhausted")
            self._locks[parent.context_ref] = asyncio.Lock()
        async with self._locks[parent.context_ref]:
            self._parent(invocation)
            read_context, plan, child_id, call_type = dispatch_contract(parent.agent_version)
            selected, _ = plan(read_context(parent))
            calls = []
            for target in selected:
                await self._admission()
                self._parent(invocation)
                run_id = child_id(parent.context_ref, target.agent_id)
                common = {**target.model_dump(), "run_id": run_id}
                current = self._registry.get(target.agent_id, target.agent_version)
                if (
                    current.manifest_ref != target.manifest_ref
                    or self._registry.lifecycle(target.agent_id, target.agent_version)
                    == "QUARANTINED"
                ):
                    calls.append(call_type(**common, status="NOT_STARTED_PERMISSION"))
                    continue
                runtime = self._runtime()
                try:
                    prior = runtime.receipt(run_id)
                except KeyError:
                    prior = None
                if prior is not None:
                    if (prior.agent_id, prior.agent_version, prior.task_id, prior.manifest_ref) != (
                        target.agent_id,
                        target.agent_version,
                        parent.task_id,
                        target.manifest_ref,
                    ):
                        raise ValueError("Supervisor child reservation identity mismatch")
                    receipt = await runtime.resume(run_id)
                else:
                    try:
                        child = await self._compile_child(target.agent_id, parent.task_id)
                    except PermissionError:
                        calls.append(call_type(**common, status="NOT_STARTED_PERMISSION"))
                        continue
                    except (ValueError, OSError, sqlite3.Error):
                        calls.append(call_type(**common, status="NOT_STARTED_SOURCE"))
                        continue
                    self._parent(invocation)
                    if (
                        child.agent_id,
                        child.agent_version,
                        child.task_id,
                        child.scope,
                        child.manifest_ref,
                    ) != (
                        target.agent_id,
                        target.agent_version,
                        parent.task_id,
                        "PAPER",
                        target.manifest_ref,
                    ):
                        raise ValueError("Supervisor child context identity mismatch")
                    receipt = await runtime.run(run_id, child)
                if self._observe is not None:
                    await self._observe(receipt)
                calls.append(call_type.from_receipt(receipt))
            return {"calls": [row.model_dump(mode="json") for row in calls]}

"""Bound parent orchestration of independently admitted, persisted child graphs."""

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
    if version == "0.5.0":
        from ai_quant_os.task_supervisor_incident import (
            read_context,
            wake_plan as incident_task_wake_plan,
            child_run_id as incident_task_child_id,
            TaskSpecialistCall,
        )

        return (
            read_context,
            incident_task_wake_plan,
            incident_task_child_id,
            TaskSpecialistCall,
            "PAPER",
        )
    if version == "0.6.0":
        from ai_quant_os.task_supervisor_research import (
            read_context,
            wake_plan as research_task_wake_plan,
            child_run_id as research_task_child_id,
            TaskSpecialistCall,
        )

        return (
            read_context,
            research_task_wake_plan,
            research_task_child_id,
            TaskSpecialistCall,
            "SANDBOX",
        )

    if version == "0.7.0":
        from ai_quant_os.task_supervisor_research import (
            read_context_v3 as read_context_v3,
            wake_plan_v3 as wake_plan_v3,
            child_run_id_v3 as child_run_id_v3,
            TaskSpecialistCallV3 as TaskSpecialistCallV3,
        )

        return read_context_v3, wake_plan_v3, child_run_id_v3, TaskSpecialistCallV3, "SANDBOX"

    if version == "0.8.0":
        from ai_quant_os.task_supervisor_research import (
            read_context_v4,
            wake_plan_v4,
            child_run_id_v4,
            TaskSpecialistCallV4,
        )

        return read_context_v4, wake_plan_v4, child_run_id_v4, TaskSpecialistCallV4, "SANDBOX"

    if version == "0.9.0":
        from ai_quant_os.task_supervisor_research import (
            read_context_v5,
            wake_plan_v5,
            child_run_id_v5,
            TaskSpecialistCallV5,
        )

        return read_context_v5, wake_plan_v5, child_run_id_v5, TaskSpecialistCallV5, "SANDBOX"

    if version == "0.4.0":
        from ai_quant_os.task_supervisor import read_context, wake_plan as task_wake_plan
        from ai_quant_os.supervisor_drift import child_run_id as task_child_id, DriftSpecialistCall

        return read_context, task_wake_plan, task_child_id, DriftSpecialistCall, "PAPER"
    if version == "0.3.0":
        from ai_quant_os.supervisor_drift import (
            read_context,
            wake_plan as drift_wake_plan,
            child_run_id as drift_child_id,
            DriftSpecialistCall,
        )

        return read_context, drift_wake_plan, drift_child_id, DriftSpecialistCall, "PAPER"
    if version == "0.2.0":
        return read_supervisor_context, wake_plan, child_run_id, SpecialistCall, "PAPER"
    raise PermissionError("Unsupported supervisor dispatch version")


class SupervisorDispatch:
    def __init__(
        self,
        registry: AgentRegistry,
        *,
        runtime: Callable[[], GraphRuntime],
        context: Callable[[str], CompiledContext],
        compile_child: Callable[[str, str], Awaitable[CompiledContext]],
        compile_child_versioned: Callable[[str, str, str], Awaitable[CompiledContext]]
        | None = None,
        compile_child_parent_aware: Callable[
            [CompiledContext, str, str, str], Awaitable[CompiledContext]
        ]
        | None = None,
        admission: Callable[[], Awaitable[None]],
        observe: Callable[[GraphReceipt], Awaitable[None]] | None = None,
    ):
        self._registry, self._runtime, self._context = registry, runtime, context
        self._compile_child, self._compile_child_versioned = compile_child, compile_child_versioned
        self._compile_child_parent_aware = compile_child_parent_aware
        self._admission = admission
        self._observe = observe
        self._locks: dict[str, asyncio.Lock] = {}

    def _parent(self, invocation: ToolInvocation) -> CompiledContext:
        read_context, _, _, _, scope = dispatch_contract(invocation.agent_version)
        if (invocation.agent_id, invocation.scope, invocation.tool) != (
            "A01",
            scope,
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
            "A01", invocation.agent_version, "TASK_ORCHESTRATE", scope=scope
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
            read_context, plan, child_id, call_type, scope = dispatch_contract(parent.agent_version)
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
                        if parent.agent_version in {"0.8.0", "0.9.0"}:
                            if self._compile_child_parent_aware is None:
                                raise PermissionError("Bound supervisor child unavailable")
                            evidence = read_context(parent)
                            source_ref = (
                                evidence.task_spec.artifact_ref
                                if parent.agent_version == "0.8.0"
                                else evidence.task_spec.review_ref
                            )
                            child = await self._compile_child_parent_aware(
                                parent,
                                target.agent_id,
                                target.agent_version,
                                source_ref,
                            )
                        elif (
                            self._compile_child_versioned is not None
                            and parent.agent_version == "0.7.0"
                        ):
                            child = await self._compile_child_versioned(
                                target.agent_id, target.agent_version, parent.task_id
                            )
                        else:
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
                        scope,
                        target.manifest_ref,
                    ):
                        raise ValueError("Supervisor child context identity mismatch")
                    receipt = await runtime.run(run_id, child)
                if self._observe is not None:
                    await self._observe(receipt)
                calls.append(call_type.from_receipt(receipt))
            return {"calls": [row.model_dump(mode="json") for row in calls]}

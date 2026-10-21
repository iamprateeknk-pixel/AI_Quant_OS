"""Current task and role admission for checked validation-result retrieval."""

import asyncio
from datetime import datetime
from typing import Callable
from uuid import uuid4

from ai_quant_os.agent_context import CompiledContext, require_context_for_agent
from ai_quant_os.agent_registry import AgentRegistry, ToolInvocation
from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.health_watchdog import owned_health_call
from ai_quant_os.holdout_validator_rpc import (
    HoldoutRequest,
    HoldoutResponse,
    verify_response,
)
from ai_quant_os.validation_package_store import ValidationPackageStore
from ai_quant_os.validation_scientist_sources import summary_for_context, validation_summary


class IsolatedValidationRun:
    """Delegate A10 archive verification to the service without opening its stores."""

    def __init__(
        self,
        client,
        registry: AgentRegistry,
        *,
        context: Callable[[str], CompiledContext],
        clock: Callable[[], datetime],
    ) -> None:
        self._client, self._registry = client, registry
        self._context, self._clock = context, clock

    async def __call__(self, invocation: ToolInvocation) -> dict:
        if (
            (invocation.agent_id, invocation.agent_version, invocation.scope, invocation.tool)
            != ("A10", "0.2.0", "SANDBOX", "VALIDATION_RUN")
            or invocation.payload
            or not invocation.context_ref
            or not invocation.task_id
        ):
            raise PermissionError("Unsupported validation-result authority")
        context = self._context(invocation.context_ref)
        if context.context_ref != invocation.context_ref:
            raise PermissionError("Validation context identity mismatch")

        def admit():
            require_context_for_agent(
                context,
                registry=self._registry,
                agent_id="A10",
                agent_version="0.2.0",
                task_id=invocation.task_id,
                scope="SANDBOX",
                now=self._clock(),
            )
            self._registry.require_tool("A10", "0.2.0", "VALIDATION_RUN", scope="SANDBOX")

        admit()
        summary = summary_for_context(context)
        request = HoldoutRequest(
            request_id=uuid4().hex,
            operation="VALIDATION_RUN",
            agent_id="A10",
            agent_version="0.2.0",
            task_id=invocation.task_id,
            context_ref=context.context_ref,
            candidate_ref=summary.candidate_ref,
            training_ref=summary.training_ref,
            holdout_ref=summary.holdout_ref,
            evidence_ref=summary.artifact_ref,
        )
        response = await asyncio.to_thread(self._client.call, request)
        if type(response) is not HoldoutResponse:
            raise ValueError("Invalid HOLDOUT validator response")
        verify_response(request, response)
        admit()
        if (
            response.evidence_ref != summary.artifact_ref
            or response.summary_sha256 != summary.summary_sha256
            or response.aggregate.outcome != summary.outcome
            or response.aggregate.gate_count != len(summary.gates)
        ):
            raise ValueError("Validation service result differs from bound context")
        return {
            "artifact_ref": summary.artifact_ref,
            "summary_sha256": summary.summary_sha256,
            "status": "CHECKED_ARCHIVED_OBSERVATION",
        }


class ValidationRun:
    def __init__(
        self,
        store: CandidateValidationStore,
        packages: ValidationPackageStore,
        registry: AgentRegistry,
        *,
        context: Callable[[str], CompiledContext],
        clock: Callable[[], datetime],
    ) -> None:
        self._store, self._packages, self._registry = store, packages, registry
        self._context, self._clock = context, clock

    async def __call__(self, invocation: ToolInvocation) -> dict:
        if (
            (invocation.agent_id, invocation.agent_version, invocation.scope, invocation.tool)
            != ("A10", "0.2.0", "SANDBOX", "VALIDATION_RUN")
            or invocation.payload
            or not invocation.context_ref
            or not invocation.task_id
        ):
            raise PermissionError("Unsupported validation-result authority")
        context = self._context(invocation.context_ref)
        if context.context_ref != invocation.context_ref:
            raise PermissionError("Validation context identity mismatch")

        def admit():
            require_context_for_agent(
                context,
                registry=self._registry,
                agent_id="A10",
                agent_version="0.2.0",
                task_id=invocation.task_id,
                scope="SANDBOX",
                now=self._clock(),
            )
            self._registry.require_tool("A10", "0.2.0", "VALIDATION_RUN", scope="SANDBOX")

        admit()
        expected = summary_for_context(context)

        def capture():
            admit()
            artifact = self._store.get(expected.artifact_ref)
            package = self._packages.get(expected.package_ref)
            if artifact is None or package is None:
                raise ValueError("Original validation artifact or package unavailable")
            return validation_summary(package, artifact)

        saved = await owned_health_call(capture)
        admit()
        if saved != expected:
            raise ValueError("Validation archive differs from compiled context")
        return {
            "artifact_ref": saved.artifact_ref,
            "summary_sha256": saved.summary_sha256,
            "status": "CHECKED_ARCHIVED_OBSERVATION",
        }

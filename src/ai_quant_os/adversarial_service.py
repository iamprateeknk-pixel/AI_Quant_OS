"""Task-bound, read-only verification for the A11 archived stress tool."""

import asyncio
from datetime import datetime
from hashlib import sha256
from typing import Callable
from uuid import uuid4

from ai_quant_os.adversarial_agent import _observations, adversarial_sources
from ai_quant_os.agent_context import (
    CompiledContext,
    bind_resource_provenance,
    require_context_for_agent,
)
from ai_quant_os.agent_registry import AgentRegistry, ToolInvocation
from ai_quant_os.holdout_validator_rpc import (
    HoldoutRequest,
    HoldoutResponse,
    stress_summary_sha256,
    verify_response,
)
from ai_quant_os.research_adversarial import verify_adversarial
from ai_quant_os.research_results import ResearchReport
from ai_quant_os.research_store import ResearchStore, encode_research


class StressRun:
    def __init__(
        self,
        store: ResearchStore | Callable[[], ResearchStore | None],
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
            != ("A11", "0.2.0", "SANDBOX", "STRESS_RUN")
            or not invocation.context_ref
            or not invocation.task_id
        ):
            raise PermissionError("Unsupported adversarial stress authority")
        context = self._context(invocation.context_ref)
        if context.context_ref != invocation.context_ref:
            raise PermissionError("Adversarial context identity mismatch")

        def admit():
            require_context_for_agent(
                context,
                registry=self._registry,
                agent_id="A11",
                agent_version="0.2.0",
                task_id=invocation.task_id,
                scope="SANDBOX",
                now=self._clock(),
            )
            self._registry.require_tool("A11", "0.2.0", "STRESS_RUN", scope="SANDBOX")

        admit()
        candidate, _, stress = _observations(context)
        if invocation.payload != {
            "experiment_id": candidate.experiment_id,
            "report_sha256": candidate.report_sha256,
        }:
            raise PermissionError("Adversarial request differs from task evidence")
        store = self._store() if callable(self._store) else self._store
        if store is None:
            raise ValueError("Research archive is unavailable")
        report = store.get(candidate.experiment_id)
        if type(report) is not ResearchReport or report.schema_version != "1.2":
            raise ValueError("Original adversarial archive unavailable")
        if sha256(encode_research(report)).hexdigest() != candidate.report_sha256:
            raise ValueError("Adversarial archive differs from compiled evidence")
        expected_sources = adversarial_sources(report, now=candidate.as_of)
        for row, name in zip(
            context.inputs,
            ("strategy_candidate", "validation_results", "stress_library"),
            strict=True,
        ):
            expected = await expected_sources[name].fetch()
            binding = expected_sources[name]
            expected = bind_resource_provenance(expected, binding)
            if (
                row.payload_json != expected.payload_json
                or row.source_id != expected.source_id
                or row.content_ref != expected.content_ref
                or row.source_tool != binding.tool
                or row.resource_class != binding.resource_class
                or row.as_of != expected.as_of
                or row.valid_until != expected.valid_until
            ):
                raise ValueError("Adversarial source differs from verified archive")
        verify_adversarial(report.adversarial, report.plan, report.holdout, report.attempts)
        admit()
        return {
            "experiment_id": candidate.experiment_id,
            "report_sha256": candidate.report_sha256,
            "state": stress.state,
            "reason": stress.reason,
            "scenarios": [row.model_dump(mode="json") for row in stress.scenarios],
            "status": "CHECKED_ARCHIVED_STRESS",
        }


class IsolatedStressRun:
    """Delegate A11 archive verification to the isolated validator service."""

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
            != ("A11", "0.2.0", "SANDBOX", "STRESS_RUN")
            or not invocation.context_ref
            or not invocation.task_id
        ):
            raise PermissionError("Unsupported adversarial stress authority")
        context = self._context(invocation.context_ref)
        if context.context_ref != invocation.context_ref:
            raise PermissionError("Adversarial context identity mismatch")

        def admit():
            require_context_for_agent(
                context,
                registry=self._registry,
                agent_id="A11",
                agent_version="0.2.0",
                task_id=invocation.task_id,
                scope="SANDBOX",
                now=self._clock(),
            )
            self._registry.require_tool("A11", "0.2.0", "STRESS_RUN", scope="SANDBOX")

        admit()
        candidate, _validation, stress = _observations(context)
        if invocation.payload != {
            "experiment_id": candidate.experiment_id,
            "report_sha256": candidate.report_sha256,
        }:
            raise PermissionError("Adversarial request differs from task evidence")
        if stress.training_ref is None or stress.holdout_ref is None:
            raise ValueError("Adversarial context lacks isolated dataset scope references")
        evidence_ref = f"research-report:{candidate.experiment_id}:{candidate.report_sha256}"
        request = HoldoutRequest(
            request_id=uuid4().hex,
            operation="STRESS_RUN",
            agent_id="A11",
            agent_version="0.2.0",
            task_id=invocation.task_id,
            context_ref=context.context_ref,
            candidate_ref=candidate.experiment_id,
            training_ref=stress.training_ref,
            holdout_ref=stress.holdout_ref,
            evidence_ref=evidence_ref,
        )
        response = await asyncio.to_thread(self._client.call, request)
        if type(response) is not HoldoutResponse:
            raise ValueError("Invalid HOLDOUT validator response")
        verify_response(request, response)
        admit()
        expected_scenarios = tuple(row.model_dump(mode="json") for row in stress.scenarios)
        if (
            response.evidence_ref != evidence_ref
            or response.summary_sha256
            != stress_summary_sha256(stress.state, stress.reason, expected_scenarios)
            or response.aggregate.state != stress.state
            or response.aggregate.reason_codes != (stress.reason,)
            or response.aggregate.scenario_count != len(expected_scenarios)
            or tuple(row.model_dump(mode="json") for row in response.aggregate.scenarios)
            != expected_scenarios
        ):
            raise ValueError("Adversarial service result differs from bound context")
        return {
            "experiment_id": candidate.experiment_id,
            "report_sha256": candidate.report_sha256,
            "state": stress.state,
            "reason": stress.reason,
            "scenarios": list(expected_scenarios),
            "status": "CHECKED_ARCHIVED_STRESS",
        }

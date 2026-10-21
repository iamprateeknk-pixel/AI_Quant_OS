"""The A11 mission rechecks archived native stress evidence without rerunning candidates."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest


def report_fixture():
    from pydantic import TypeAdapter
    from ai_quant_os.research_results import ResearchReport

    payload = json.loads(
        (
            Path(__file__).parents[1] / "docs/evaluations/research-adversarial-baseline.json"
        ).read_text()
    )
    return TypeAdapter(ResearchReport).validate_python(payload["report"])


def test_adversarial_mission_rechecks_source_bound_archived_stress():
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.adversarial_agent import adversarial_implementation, adversarial_sources
    from ai_quant_os.graph_runtime import AgentInvocation

    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    report = report_fixture()
    registry = load_agent_catalog()
    context = asyncio.run(
        ContextCompiler(registry, adversarial_sources(report, now=now), clock=lambda: now).compile(
            "A11", "0.2.0", task_id="stress-review", scope="SANDBOX"
        )
    )
    expected_ref = adversarial_sources(report, now=now)["stress_library"]

    async def fetch():
        return await expected_ref.fetch()

    assert context.status == "READY"

    class Tools:
        async def call(self, tool, payload):
            assert tool == "STRESS_RUN"
            assert payload == {
                "experiment_id": report.plan.experiment_id,
                "report_sha256": __import__("hashlib")
                .sha256(
                    __import__(
                        "ai_quant_os.research_store", fromlist=["encode_research"]
                    ).encode_research(report)
                )
                .hexdigest(),
            }
            facts = json.loads((await fetch()).payload_json)
            return {
                "experiment_id": report.plan.experiment_id,
                "report_sha256": payload["report_sha256"],
                "state": facts["state"],
                "reason": facts["reason"],
                "scenarios": facts["scenarios"],
                "status": "CHECKED_ARCHIVED_STRESS",
            }

    implementation = adversarial_implementation(registry)
    output = asyncio.run(implementation.invoke(AgentInvocation(context, Tools())))
    output.require_context(context)
    assert output.adversarial_score == report.adversarial.state
    assert output.candidate_fingerprint == report.holdout.binding.implementation_sha256
    assert output.assessment == "NO_FAILURE_FOUND"
    assert len(output.scenarios) == len(report.adversarial.scenarios)
    assert output.promotion_allowed is False


def test_adversarial_mission_refuses_changed_stress_receipt():
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.adversarial_agent import adversarial_implementation, adversarial_sources
    from ai_quant_os.graph_runtime import AgentInvocation

    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    report = report_fixture()
    registry = load_agent_catalog()
    context = asyncio.run(
        ContextCompiler(registry, adversarial_sources(report, now=now), clock=lambda: now).compile(
            "A11", "0.2.0", task_id="stress-review", scope="SANDBOX"
        )
    )

    class Tools:
        async def call(self, tool, payload):
            return {"status": "CHECKED_ARCHIVED_STRESS", "report_sha256": "0" * 64}

    with pytest.raises(ValueError, match="stress receipt"):
        asyncio.run(adversarial_implementation(registry).invoke(AgentInvocation(context, Tools())))


def test_adversarial_sources_require_verified_native_scenarios():
    from dataclasses import replace
    from ai_quant_os.adversarial_agent import adversarial_sources

    report = report_fixture()
    with pytest.raises(ValueError, match="Adversarial source evidence missing"):
        adversarial_sources(replace(report, adversarial=None), now=datetime.now(timezone.utc))


def test_adversarial_sources_accept_checked_snapshot_without_research_report():
    from ai_quant_os.adversarial_agent import (
        CandidateObservation,
        StressObservation,
        ValidationObservation,
        adversarial_sources,
        adversarial_sources_from_snapshot,
    )

    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    report = report_fixture()
    original = adversarial_sources(report, now=now)
    payloads = {
        name: json.loads(asyncio.run(binding.fetch()).payload_json)
        for name, binding in original.items()
    }
    rebuilt = adversarial_sources_from_snapshot(
        CandidateObservation.model_validate(payloads["strategy_candidate"]),
        ValidationObservation.model_validate(payloads["validation_results"]),
        StressObservation.model_validate(payloads["stress_library"]),
        holdout_instrument_id=report.plan.holdout_scope.instrument_id,
        holdout_first_observed_at=report.plan.holdout_scope.first_observed_at,
        holdout_last_observed_at=report.plan.holdout_scope.last_observed_at,
        holdout_dataset_fingerprint=report.plan.holdout_scope.dataset_fingerprint,
        now=now,
    )

    assert tuple(rebuilt) == tuple(original)
    for name in original:
        assert asyncio.run(rebuilt[name].fetch()) == asyncio.run(original[name].fetch())


def test_stress_tool_rechecks_original_archive_and_current_context():
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import ToolInvocation, load_agent_catalog
    from ai_quant_os.adversarial_agent import adversarial_sources
    from ai_quant_os.adversarial_service import StressRun

    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    report = report_fixture()
    registry = load_agent_catalog()
    context = asyncio.run(
        ContextCompiler(registry, adversarial_sources(report, now=now), clock=lambda: now).compile(
            "A11", "0.2.0", task_id="stress-review", scope="SANDBOX"
        )
    )

    class Store:
        def get(self, experiment_id):
            return report if experiment_id == report.plan.experiment_id else None

    tool = StressRun(Store(), registry, context=lambda ref: context, clock=lambda: now)
    candidate = __import__(
        "ai_quant_os.adversarial_agent", fromlist=["_observations"]
    )._observations(context)[0]
    invocation = ToolInvocation(
        "A11",
        "0.2.0",
        "STRESS_RUN",
        "SANDBOX",
        {"experiment_id": candidate.experiment_id, "report_sha256": candidate.report_sha256},
        context.context_ref,
        context.task_id,
    )
    receipt = asyncio.run(tool(invocation))
    assert receipt["status"] == "CHECKED_ARCHIVED_STRESS"
    assert receipt["state"] == report.adversarial.state

    changed = ToolInvocation(
        "A11",
        "0.2.0",
        "STRESS_RUN",
        "SANDBOX",
        {"experiment_id": "another-experiment", "report_sha256": candidate.report_sha256},
        context.context_ref,
        context.task_id,
    )
    with pytest.raises(PermissionError, match="differs from task evidence"):
        asyncio.run(tool(changed))


def test_isolated_stress_tool_binds_scope_and_checks_service_aggregate():
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import ToolInvocation, load_agent_catalog
    from ai_quant_os.adversarial_agent import _observations, adversarial_sources
    from ai_quant_os.adversarial_service import IsolatedStressRun
    from ai_quant_os.holdout_validator_rpc import HoldoutResponse, stress_summary_sha256

    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    report = report_fixture()
    registry = load_agent_catalog()
    context = asyncio.run(
        ContextCompiler(registry, adversarial_sources(report, now=now), clock=lambda: now).compile(
            "A11", "0.2.0", task_id="stress-review", scope="SANDBOX"
        )
    )
    candidate, _validation, stress = _observations(context)

    class Client:
        requests = []
        alter_response = False

        def call(self, request):
            self.requests.append(request)
            scenarios = [row.model_dump(mode="json") for row in stress.scenarios]
            if self.alter_response:
                scenarios[0]["net_pnl"] = "0"
            return HoldoutResponse.model_validate_json(
                json.dumps(
                    {
                        "request_id": request.request_id,
                        "task_id": request.task_id,
                        "context_ref": request.context_ref,
                        "status": "CHECKED_ARCHIVED_STRESS",
                        "evidence_ref": request.evidence_ref,
                        "summary_sha256": stress_summary_sha256(
                            stress.state, stress.reason, scenarios
                        ),
                        "aggregate": {
                            "state": stress.state,
                            "scenario_count": len(scenarios),
                            "reason_codes": [stress.reason],
                            "scenarios": scenarios,
                        },
                    }
                )
            )

    client = Client()
    tool = IsolatedStressRun(
        client, registry, context=lambda _reference: context, clock=lambda: now
    )
    invocation = ToolInvocation(
        agent_id="A11",
        agent_version="0.2.0",
        scope="SANDBOX",
        tool="STRESS_RUN",
        payload={
            "experiment_id": candidate.experiment_id,
            "report_sha256": candidate.report_sha256,
        },
        context_ref=context.context_ref,
        task_id=context.task_id,
    )
    result = asyncio.run(tool(invocation))

    assert result["status"] == "CHECKED_ARCHIVED_STRESS"
    assert result["scenarios"] == [row.model_dump(mode="json") for row in stress.scenarios]
    request = client.requests[0]
    assert request.operation == "STRESS_RUN"
    assert request.training_ref == stress.training_ref
    assert request.holdout_ref == stress.holdout_ref
    assert request.evidence_ref.endswith(candidate.report_sha256)
    assert (request.task_id, request.context_ref) == (context.task_id, context.context_ref)

    client.alter_response = True
    with pytest.raises(ValueError, match="differs from bound context"):
        asyncio.run(tool(invocation))

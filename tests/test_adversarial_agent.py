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

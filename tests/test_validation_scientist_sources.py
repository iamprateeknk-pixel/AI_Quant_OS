"""Holdout metadata stays outside candidate design and binds advisory outputs."""

import asyncio
from datetime import timedelta

import pytest

from ai_quant_os.agent_context import ContextCompiler, SourceBinding
from ai_quant_os.agent_registry import load_agent_catalog, ToolInvocation
from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.validation_package_store import ValidationPackageStore
from ai_quant_os.validation_scientist_agent import ValidationScorecard
from ai_quant_os.validation_scientist_service import ValidationRun
from ai_quant_os.validation_scientist_sources import validation_sources, summary_for_context
from test_candidate_validation_store import failed_tests
from test_strategy_prefix_worker import package as package
from test_research_hypothesis_api import NOW


def compiled(package, failure):
    return asyncio.run(
        ContextCompiler(
            load_agent_catalog(), validation_sources(package, failure, now=NOW), clock=lambda: NOW
        ).compile("A10", "0.2.0", task_id="validation", scope="SANDBOX")
    )


@pytest.mark.parametrize("agent_id, tool", [("A08", "HISTORY_READ"), ("A09", "WORLD_STATE_READ")])
def test_holdout_validation_aliases_are_denied_before_candidate_design_fetch(
    package, agent_id, tool
):
    registry = load_agent_catalog()
    fetched = []

    async def fetch():
        fetched.append(True)
        raise AssertionError("Candidate design must not fetch validation results")

    sources = {
        name: SourceBinding(tool, "HOLDOUT", fetch)
        for name in registry.get(agent_id, "0.2.0").required_inputs
    }
    context = asyncio.run(
        ContextCompiler(registry, sources, clock=lambda: NOW).compile(
            agent_id, "0.2.0", task_id="design", scope="SANDBOX"
        )
    )
    assert context.status == "DEGRADED" and all(row.status == "DENIED" for row in context.inputs)
    assert fetched == []


def test_validation_output_cannot_substitute_original_result_or_metrics(package):
    with CandidateValidationStore() as archive:
        failure = archive.put_failure(package, failed_tests(package))
    context = compiled(package, failure)
    summary = summary_for_context(context)
    output = ValidationScorecard(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        contradictions=summary.reasons,
        trace_id="fixture",
        summary=summary,
    )
    output.require_context(context)
    forged = output.model_copy(
        update={
            "summary": summary.model_copy(
                update={"artifact_ref": "candidate-prerequisite-failure:" + "0" * 64}
            )
        }
    )
    with pytest.raises(ValueError):
        ValidationScorecard.model_validate_json(forged.model_dump_json()).require_context(context)
    for name in ("promotion_allowed", "isolation_verified"):
        with pytest.raises(ValueError):
            ValidationScorecard.model_validate_json(
                output.model_copy(update={name: 1}).model_dump_json()
            )


def test_future_package_cannot_be_labeled_current_archival_evidence(package):
    with CandidateValidationStore() as archive:
        failure = archive.put_failure(package, failed_tests(package))
    with pytest.raises(PermissionError):
        validation_sources(package, failure, now=package.captured_at - timedelta(seconds=1))


@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": "other"},
        {"agent_id": "A09"},
        {"scope": "PAPER"},
        {"payload": {"artifact_ref": "replace"}},
        {"context_ref": None},
    ],
)
def test_validation_tool_refuses_unbound_authority_before_archive_read(package, changes):
    with CandidateValidationStore() as archive, ValidationPackageStore() as packages:
        failure = archive.put_failure(package, failed_tests(package))
        context = compiled(package, failure)
        tool = ValidationRun(
            archive, packages, load_agent_catalog(), context=lambda ref: context, clock=lambda: NOW
        )
        fields = dict(
            agent_id="A10",
            agent_version="0.2.0",
            scope="SANDBOX",
            tool="VALIDATION_RUN",
            payload={},
            context_ref=context.context_ref,
            task_id=context.task_id,
        )
        with pytest.raises(PermissionError):
            asyncio.run(tool(ToolInvocation(**{**fields, **changes})))

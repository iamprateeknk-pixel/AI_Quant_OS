"""Holdout metadata stays outside candidate design and binds advisory outputs."""

import asyncio
from datetime import timedelta

import pytest

from ai_quant_os.agent_context import ContextCompiler, SourceBinding
from ai_quant_os.agent_context import CompiledContext, ContextInput, EvidenceRecord
from ai_quant_os.agent_registry import load_agent_catalog, ToolInvocation
from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.validation_package_store import ValidationPackageStore
from ai_quant_os.validation_scientist_agent import ValidationScorecard
from ai_quant_os.validation_scientist_service import IsolatedValidationRun, ValidationRun
from ai_quant_os.validation_scientist_sources import (
    summary_for_context,
    validation_sources,
    validation_sources_from_summary,
)
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


def test_service_summary_and_receipt_build_the_same_validation_context():
    from hashlib import sha256

    from ai_quant_os.candidate_validation_contracts import GATE_NAMES
    from ai_quant_os.research_data_contracts import ResearchDataReceipt, research_dataset_ref
    from ai_quant_os.validation_scientist_contracts import (
        ValidationGateSummary,
        ValidationSummary,
    )

    raw_sha256 = sha256(b"holdout fixture bytes").hexdigest()
    receipt = ResearchDataReceipt(
        dataset_ref=research_dataset_ref("source-fixture", raw_sha256, "HOLDOUT"),
        source_id="source-fixture",
        resource_class="HOLDOUT",
        raw_sha256=raw_sha256,
        dataset_fingerprint="1" * 64,
        instrument_id="EURUSD",
        row_count=2,
        first_observed_at=NOW - timedelta(minutes=2),
        last_observed_at=NOW - timedelta(minutes=1),
        received_at=NOW - timedelta(seconds=30),
    )
    summary = ValidationSummary(
        artifact_ref="candidate-prerequisite-failure:" + "2" * 64,
        package_ref="validation-package:" + "3" * 64,
        candidate_ref="strategy-candidate:" + "4" * 64,
        training_ref="research-data:" + "5" * 64,
        holdout_ref=receipt.dataset_ref,
        outcome="PREREQUISITE_FAILED",
        gates=tuple(
            ValidationGateSummary(
                name=name,
                state="FAIL" if index == 0 else "PENDING",
                reason="PREREQUISITE_NOT_SATISFIED",
            )
            for index, name in enumerate(GATE_NAMES)
        ),
        reasons=("PREREQUISITE_NOT_SATISFIED",),
        statistics=None,
    )

    context = asyncio.run(
        ContextCompiler(
            load_agent_catalog(),
            validation_sources_from_summary(summary, receipt, captured_at=NOW, now=NOW),
            clock=lambda: NOW,
        ).compile("A10", "0.2.0", task_id="validation", scope="SANDBOX")
    )

    assert context.status == "READY"
    assert summary_for_context(context) == summary
    assert tuple(row.source_tool for row in context.inputs) == ("HOLDOUT_READ",) * 3
    assert tuple(row.resource_class for row in context.inputs) == ("HOLDOUT",) * 3

    from pydantic import ValidationError

    training_receipt = receipt.model_copy(
        update={
            "dataset_ref": research_dataset_ref("source-fixture", raw_sha256, "TRAINING"),
            "resource_class": "TRAINING",
        }
    )
    with pytest.raises((PermissionError, ValidationError)):
        validation_sources_from_summary(summary, training_receipt, captured_at=NOW, now=NOW)
    with pytest.raises(PermissionError, match="Future validation packages"):
        validation_sources_from_summary(
            summary, receipt, captured_at=NOW + timedelta(seconds=1), now=NOW
        )
    with pytest.raises(PermissionError, match="receipt binding"):
        validation_sources_from_summary(
            summary,
            receipt,
            captured_at=receipt.received_at - timedelta(seconds=1),
            now=NOW,
        )


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


def test_isolated_validation_tool_sends_only_bound_refs_and_checks_service_aggregate():
    from ai_quant_os.holdout_validator_rpc import HoldoutResponse
    from ai_quant_os.candidate_validation_contracts import GATE_NAMES
    from ai_quant_os.validation_scientist_contracts import ValidationGateSummary, ValidationSummary
    from ai_quant_os.validation_scientist_sources import ValidationObservation
    from ai_quant_os.agent_context import _context_ref

    summary = ValidationSummary(
        artifact_ref="candidate-prerequisite-failure:" + "1" * 64,
        package_ref="validation-package:" + "2" * 64,
        candidate_ref="strategy-candidate:" + "3" * 64,
        training_ref="research-data:" + "4" * 64,
        holdout_ref="research-data:" + "5" * 64,
        outcome="PREREQUISITE_FAILED",
        gates=tuple(
            ValidationGateSummary(
                name=name,
                state="FAIL" if index == 0 else "PENDING",
                reason="PREREQUISITE_NOT_SATISFIED",
            )
            for index, name in enumerate(GATE_NAMES)
        ),
        reasons=("PREREQUISITE_NOT_SATISFIED",),
        statistics=None,
    )
    inputs = []
    for name in ("strategy_candidate", "immutable_datasets", "execution_models"):
        payload = ValidationObservation(as_of=NOW, input_name=name, summary=summary)
        record = EvidenceRecord(
            source_id="fixture:" + name,
            as_of=NOW,
            valid_until=NOW + timedelta(seconds=5),
            trusted=True,
            payload_json=payload.model_dump_json(),
        )
        inputs.append(
            ContextInput(
                input_name=name,
                status="AVAILABLE",
                resource_class="HOLDOUT",
                source_tool="HOLDOUT_READ",
                source_id=record.source_id,
                as_of=record.as_of,
                valid_until=record.valid_until,
                content_ref=record.content_ref,
                payload_json=record.payload_json,
            )
        )
    context = CompiledContext(
        context_ref="agent-context:" + "0" * 64,
        agent_id="A10",
        agent_version="0.2.0",
        manifest_ref=load_agent_catalog().get("A10", "0.2.0").manifest_ref,
        task_id="validation",
        scope="SANDBOX",
        status="READY",
        inputs=tuple(inputs),
        input_refs=tuple(row.content_ref for row in inputs),
        created_at=NOW,
        valid_until=NOW + timedelta(seconds=4),
    )
    context = context.model_copy(update={"context_ref": _context_ref(context)})
    expected = summary_for_context(context)

    class ServiceClient:
        requests = []

        def call(self, request):
            self.requests.append(request)
            return HoldoutResponse(
                request_id=request.request_id,
                task_id=request.task_id,
                context_ref=request.context_ref,
                status="CHECKED_ARCHIVED_OBSERVATION",
                evidence_ref=expected.artifact_ref,
                summary_sha256=expected.summary_sha256,
                aggregate={"outcome": expected.outcome, "gate_count": len(expected.gates)},
            )

    client = ServiceClient()
    tool = IsolatedValidationRun(
        client,
        load_agent_catalog(),
        context=lambda reference: context,
        clock=lambda: NOW,
    )
    result = asyncio.run(
        tool(
            ToolInvocation(
                agent_id="A10",
                agent_version="0.2.0",
                scope="SANDBOX",
                tool="VALIDATION_RUN",
                payload={},
                context_ref=context.context_ref,
                task_id=context.task_id,
            )
        )
    )

    assert result == {
        "artifact_ref": expected.artifact_ref,
        "summary_sha256": expected.summary_sha256,
        "status": "CHECKED_ARCHIVED_OBSERVATION",
    }
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.operation == "VALIDATION_RUN"
    assert (request.candidate_ref, request.training_ref, request.holdout_ref) == (
        expected.candidate_ref,
        expected.training_ref,
        expected.holdout_ref,
    )
    assert (request.task_id, request.context_ref) == (context.task_id, context.context_ref)

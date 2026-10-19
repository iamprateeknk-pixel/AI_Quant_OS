"""Holdout-classified metadata, without candidate source or raw datasets."""

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from ai_quant_os.agent_context import (
    CompiledContext,
    DatasetResourceCatalog,
    EvidenceRecord,
    SourceBinding,
    verify_context,
)
from ai_quant_os.candidate_prerequisite_failure import CandidatePrerequisiteFailure
from ai_quant_os.candidate_validation_contracts import CandidateValidationScorecard, GATE_NAMES
from ai_quant_os.contracts import _require_aware
from ai_quant_os.validation_package_contracts import ValidationPackage
from ai_quant_os.validation_scientist_contracts import (
    ValidationGateSummary,
    ValidationStatistics,
    ValidationSummary,
)

INPUT_NAMES = ("strategy_candidate", "immutable_datasets", "execution_models")


class ValidationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    input_name: Literal["strategy_candidate", "immutable_datasets", "execution_models"]
    historical_archive_only: Literal[True] = True
    summary: ValidationSummary


def validation_summary(
    package: ValidationPackage,
    artifact: CandidatePrerequisiteFailure | CandidateValidationScorecard,
) -> ValidationSummary:
    if type(package) is not ValidationPackage:
        raise ValueError("Original validation package required")
    package = ValidationPackage.model_validate_json(package.model_dump_json())
    if type(artifact) not in (CandidatePrerequisiteFailure, CandidateValidationScorecard):
        raise ValueError("Checked validation artifact required")
    artifact = type(artifact).model_validate_json(artifact.model_dump_json())
    if (artifact.package_ref, artifact.candidate_ref) != (
        package.package_ref,
        package.candidate.candidate_ref,
    ):
        raise ValueError("Validation summary original-source binding mismatch")
    statistics = None
    if type(artifact) is CandidatePrerequisiteFailure:
        reference = artifact.failure_ref
        reasons = (artifact.reason,)
        gates = tuple(
            ValidationGateSummary(
                name=name,
                state="FAIL" if index == 0 else "PENDING",
                reason=artifact.reason if index == 0 else "PREREQUISITE_NOT_SATISFIED",
            )
            for index, name in enumerate(GATE_NAMES)
        )
    else:
        reference, reasons = artifact.scorecard_ref, artifact.reasons
        gates = tuple(
            ValidationGateSummary(name=row.name, state=row.state, reason=row.reason)
            for row in artifact.gates
        )
        statistics = ValidationStatistics(
            holdout_net_pnl=artifact.holdout_net_pnl,
            development_net_pnl=artifact.development_net_pnl,
            nonnegative_window_fraction=artifact.nonnegative_window_fraction,
            pnl_currency=artifact.pnl_currency,
            requested_trials=artifact.monte_carlo.requested_trials,
            requested_seed=artifact.monte_carlo.requested_seed,
            closed_trade_count=artifact.monte_carlo.closed_trade_count,
        )
    return ValidationSummary(
        artifact_ref=reference,
        package_ref=package.package_ref,
        candidate_ref=package.candidate.candidate_ref,
        training_ref=package.training.receipt.dataset_ref,
        holdout_ref=package.holdout.receipt.dataset_ref,
        outcome=artifact.outcome,
        gates=gates,
        reasons=reasons,
        statistics=statistics,
    )


def validation_sources(
    package: ValidationPackage,
    artifact: CandidatePrerequisiteFailure | CandidateValidationScorecard,
    *,
    now: datetime,
) -> dict[str, SourceBinding]:
    summary = validation_summary(package, artifact)
    _require_aware(now)
    if package.captured_at > now:
        raise PermissionError("Future validation packages cannot become archival evidence")
    package = ValidationPackage.model_validate_json(package.model_dump_json())
    catalog = DatasetResourceCatalog.from_receipts((package.holdout.receipt,))
    sources = {}
    for name in INPUT_NAMES:
        payload = ValidationObservation(as_of=now, input_name=name, summary=summary)
        record = EvidenceRecord(
            source_id="candidate-validation:"
            + name
            + ":"
            + sha256(payload.model_dump_json().encode()).hexdigest(),
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=payload.model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(
            "HOLDOUT_READ",
            "HOLDOUT",
            fetch,
            resource_refs=(package.holdout.receipt.dataset_ref,),
            resource_catalog=catalog,
        )
    return sources


def summary_for_context(context: CompiledContext) -> ValidationSummary:
    verify_context(context)
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A10",
        "0.2.0",
        "SANDBOX",
        "READY",
    ):
        raise ValueError("Validation scientist requires its complete sandbox context")
    if tuple(
        (row.input_name, row.source_tool, row.resource_class) for row in context.inputs
    ) != tuple((name, "HOLDOUT_READ", "HOLDOUT") for name in INPUT_NAMES):
        raise ValueError("Validation context resource binding mismatch")
    facts = tuple(
        ValidationObservation.model_validate_json(row.payload_json) for row in context.inputs
    )
    summary = facts[0].summary
    if any(
        fact.input_name != name
        or fact.summary != summary
        or fact.as_of != facts[0].as_of
        or not fact.as_of
        <= context.created_at
        < context.valid_until
        <= fact.as_of + timedelta(seconds=5)
        for name, fact in zip(INPUT_NAMES, facts, strict=True)
    ):
        raise ValueError("Validation context observations differ")
    return summary

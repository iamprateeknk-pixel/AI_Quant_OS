"""Repeated training-set quality summaries with explicit evidence limitations."""

from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.evolution_evaluation import EvolutionScorecard
from ai_quant_os.evolution_protocols import EvaluationProtocol, EvolutionDataset


class TrainingQualityProfile(BaseModel):
    """A repeatability summary, not an independent quality or promotion certificate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    scope: Literal["REPEATED_TRAINING_QUALITY_DIAGNOSTIC"] = "REPEATED_TRAINING_QUALITY_DIAGNOSTIC"
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_ref: str = Field(pattern=r"^evolution-dataset:[a-f0-9]{64}$")
    baseline_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    candidate_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    primary_metric: Literal["CORRECTNESS", "REGIME_CORRECTNESS", "INTEGRITY_CORRECTNESS"]
    scorecard_refs: tuple[str, ...] = Field(min_length=2, max_length=32)
    dataset_provenance: Literal[
        "SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT", "RECORDED_REFERENCE", "REVIEWED_REFERENCE"
    ]
    first_evaluated_at: AwareDatetime
    latest_evaluated_at: AwareDatetime
    run_count: int = Field(strict=True, ge=2, le=32)
    case_count: int = Field(strict=True, ge=1, le=128)
    observed_case_count: int = Field(strict=True, ge=2, le=4096)
    baseline_correct_observations: int = Field(strict=True, ge=0, le=4096)
    candidate_correct_observations: int = Field(strict=True, ge=0, le=4096)
    baseline_accuracy: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    candidate_accuracy: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    stable_candidate_correct_cases: int = Field(strict=True, ge=0, le=128)
    variable_candidate_cases: int = Field(strict=True, ge=0, le=128)
    improving_runs: int = Field(strict=True, ge=0, le=32)
    no_improvement_runs: int = Field(strict=True, ge=0, le=32)
    guardrail_regression_runs: int = Field(strict=True, ge=0, le=32)
    evidence_independence: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    evaluation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_aggregate(self):
        if (
            self.baseline_genome_ref == self.candidate_genome_ref
            or self.scorecard_refs != tuple(sorted(set(self.scorecard_refs)))
            or len(self.scorecard_refs) != self.run_count
            or self.first_evaluated_at > self.latest_evaluated_at
            or self.observed_case_count != self.case_count * self.run_count
            or self.baseline_correct_observations > self.observed_case_count
            or self.candidate_correct_observations > self.observed_case_count
            or self.baseline_accuracy
            != Decimal(self.baseline_correct_observations) / Decimal(self.observed_case_count)
            or self.candidate_accuracy
            != Decimal(self.candidate_correct_observations) / Decimal(self.observed_case_count)
            or self.stable_candidate_correct_cases + self.variable_candidate_cases > self.case_count
            or self.improving_runs + self.no_improvement_runs + self.guardrail_regression_runs
            != self.run_count
        ):
            raise ValueError("Training quality profile differs from its repeated evidence")
        return self

    @property
    def quality_profile_ref(self) -> str:
        return "evolution-quality-profile:" + training_quality_profile_sha256(self)


def build_training_quality_profile(
    protocol: EvaluationProtocol,
    dataset: EvolutionDataset,
    scorecards: tuple[EvolutionScorecard, ...],
) -> TrainingQualityProfile:
    """Aggregate repeated complete diagnostics over one retained TRAINING dataset."""
    if type(protocol) is not EvaluationProtocol or type(dataset) is not EvolutionDataset:
        raise ValueError("Retained protocol and dataset required")
    if dataset.resource_class != "TRAINING" or protocol.dataset_ref != dataset.dataset_ref:
        raise ValueError("Quality profile requires the protocol's retained TRAINING dataset")
    if dataset.agent_id != protocol.agent_id:
        raise ValueError("Quality profile protocol and dataset agent differ")
    if not isinstance(scorecards, tuple) or not 2 <= len(scorecards) <= 32:
        raise ValueError("Quality profile requires at least two bounded scorecards")
    if any(type(row) is not EvolutionScorecard for row in scorecards):
        raise ValueError("Quality profile requires retained typed scorecards")

    ordered = tuple(sorted(scorecards, key=lambda row: (row.evaluated_at, row.scorecard_ref)))
    first = ordered[0]
    case_ids = tuple(row.case_id for row in first.case_diagnostics)
    if len({row.evaluated_at for row in ordered}) != len(ordered):
        raise ValueError("Quality profile requires distinct evaluation times")
    if len({row.scorecard_ref for row in ordered}) != len(ordered):
        raise ValueError("Quality profile scorecards must be distinct")
    for row in ordered:
        if (
            row.protocol_sha256 != protocol.protocol_sha256
            or row.dataset_ref != dataset.dataset_ref
            or row.primary_metric != protocol.primary_metric
            or row.baseline_genome_ref != first.baseline_genome_ref
            or row.challenger_genome_ref != first.challenger_genome_ref
            or row.dataset_provenance != dataset.provenance
            or tuple(case.case_id for case in row.case_diagnostics) != case_ids
        ):
            raise ValueError(
                "Quality profile scorecards must share exact protocol and case identity"
            )

    observed_cases = len(case_ids) * len(ordered)
    baseline_correct = sum(row.baseline_exact_matches for row in ordered)
    candidate_correct = sum(row.challenger_exact_matches for row in ordered)
    per_case = tuple(
        tuple(row.case_diagnostics[index].challenger_correct for row in ordered)
        for index in range(len(case_ids))
    )
    return TrainingQualityProfile(
        protocol_sha256=protocol.protocol_sha256,
        dataset_ref=dataset.dataset_ref,
        baseline_genome_ref=first.baseline_genome_ref,
        candidate_genome_ref=first.challenger_genome_ref,
        primary_metric=protocol.primary_metric,
        scorecard_refs=tuple(sorted(row.scorecard_ref for row in ordered)),
        dataset_provenance=dataset.provenance,
        first_evaluated_at=ordered[0].evaluated_at,
        latest_evaluated_at=ordered[-1].evaluated_at,
        run_count=len(ordered),
        case_count=len(case_ids),
        observed_case_count=observed_cases,
        baseline_correct_observations=baseline_correct,
        candidate_correct_observations=candidate_correct,
        baseline_accuracy=Decimal(baseline_correct) / Decimal(observed_cases),
        candidate_accuracy=Decimal(candidate_correct) / Decimal(observed_cases),
        stable_candidate_correct_cases=sum(all(rows) for rows in per_case),
        variable_candidate_cases=sum(any(rows) and not all(rows) for rows in per_case),
        improving_runs=sum(row.diagnostic == "IMPROVES_ON_TRAINING_DATA" for row in ordered),
        no_improvement_runs=sum(row.diagnostic == "NO_IMPROVEMENT" for row in ordered),
        guardrail_regression_runs=sum(row.diagnostic == "GUARDRAIL_REGRESSION" for row in ordered),
    )


def training_quality_profile_schema() -> dict[str, object]:
    schema = TrainingQualityProfile.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:ai-quant-os:evolution-training-quality-profile:1.0"
    return schema


def encode_training_quality_profile(profile: TrainingQualityProfile) -> bytes:
    return json.dumps(
        profile.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def training_quality_profile_sha256(profile: TrainingQualityProfile) -> str:
    return sha256(encode_training_quality_profile(profile)).hexdigest()

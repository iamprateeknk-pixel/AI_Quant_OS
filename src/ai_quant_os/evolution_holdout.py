"""Independent holdout evaluation for evolution challengers.

Training scorecards (:mod:`ai_quant_os.evolution_evaluation`) measure a
challenger on the same data its selection trained on, so they can never
certify quality. This module is the independent counterpart: it scores a
baseline/challenger pair on a HOLDOUT dataset under a holdout protocol whose
measurement criteria must be identical to the training protocol's, and it
fails closed unless the holdout data is provably disjoint from the training
data and was never synthetically generated for development.

Outputs are supplied by the caller (produced by
:meth:`ai_quant_os.evolution_runner.EvolutionRunner.run_holdout` in the
sealed worker) and are scored with the same deterministic adapters as
training. The resulting artifact carries no promotion authority by itself;
it is the evidence the signed promotion policy gate consumes.
"""

import json
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from ai_quant_os.agent_genomes import AgentGenome, require_genome_child
from ai_quant_os.evolution_evaluation import CaseOutput, MetricComparison, _metric_matches
from ai_quant_os.evolution_protocols import (
    EvaluationProtocol,
    EvolutionDataset,
    require_protocol_genome,
)

EVALUATOR_ID = "INDEPENDENT_HOLDOUT_EVALUATOR"
_HOLDOUT_PROVENANCE = {"RECORDED_REFERENCE", "REVIEWED_REFERENCE"}
_DETERMINISTIC_METHODS = {
    "EXACT_OBJECT_MATCH",
    "REGIME_EXACT_OBJECT_MATCH",
    "INTEGRITY_EVIDENCE_MATCH",
}


def _criteria_fingerprint(protocol: EvaluationProtocol) -> tuple:
    return tuple(
        (
            row.criterion.metric,
            row.criterion.direction,
            str(row.criterion.minimum_improvement),
            row.method,
            str(row.maximum_regression),
        )
        for row in protocol.criteria
    )


class HoldoutEvaluation(BaseModel):
    """A challenger's measured result on data its selection never trained on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    evaluator: Literal["INDEPENDENT_HOLDOUT_EVALUATOR"] = "INDEPENDENT_HOLDOUT_EVALUATOR"
    agent_id: Literal["A02", "A07"]
    training_protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    holdout_protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    training_dataset_ref: str = Field(pattern=r"^evolution-dataset:[a-f0-9]{64}$")
    holdout_dataset_ref: str = Field(pattern=r"^evolution-dataset:[a-f0-9]{64}$")
    holdout_dataset_provenance: Literal["RECORDED_REFERENCE", "REVIEWED_REFERENCE"]
    baseline_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    challenger_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    primary_metric: Literal["CORRECTNESS", "REGIME_CORRECTNESS", "INTEGRITY_CORRECTNESS"]
    baseline_score: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    challenger_score: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    case_count: int = Field(strict=True, ge=1, le=128)
    baseline_exact_matches: int = Field(strict=True, ge=0, le=128)
    challenger_exact_matches: int = Field(strict=True, ge=0, le=128)
    minimum_improvement: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    minimum_improvement_met: StrictBool
    metrics: tuple[MetricComparison, ...] = Field(min_length=1, max_length=8)
    input_overlap_verified_disjoint: Literal[True] = True
    verdict: Literal["HOLDOUT_PASS", "HOLDOUT_FAIL"]
    evaluated_at: datetime
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator("execution_verified", "promotion_allowed", mode="before")
    @classmethod
    def deny_authority(cls, value: object) -> bool:
        if value is not False:
            raise ValueError("Holdout evaluations cannot claim verification or promotion")
        return False

    @property
    def evaluation_ref(self) -> str:
        return "evolution-holdout:" + holdout_evaluation_sha256(self)


def encode_holdout_evaluation(evaluation: HoldoutEvaluation) -> bytes:
    return json.dumps(
        evaluation.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def holdout_evaluation_sha256(evaluation: HoldoutEvaluation) -> str:
    return sha256(encode_holdout_evaluation(evaluation)).hexdigest()


def evaluate_holdout_outputs(
    training_protocol: EvaluationProtocol,
    holdout_protocol: EvaluationProtocol,
    training_dataset: EvolutionDataset,
    holdout_dataset: EvolutionDataset,
    baseline: AgentGenome,
    challenger: AgentGenome,
    baseline_outputs: tuple[CaseOutput, ...],
    challenger_outputs: tuple[CaseOutput, ...],
    *,
    evaluated_at: datetime,
) -> HoldoutEvaluation:
    """Score a direct challenger against its baseline on unseen holdout data.

    Fail-closed requirements, in order: typed artifacts; the training
    protocol binds both genomes; the challenger is a direct child of the
    baseline; the holdout protocol measures with criteria identical to the
    training protocol and is bound to the holdout dataset; the training
    protocol is bound to the training dataset; the holdout dataset is a
    non-synthetic HOLDOUT set for the same agent; and no holdout case input
    or case id appears in the training dataset (no leakage).
    """
    if type(training_protocol) is not EvaluationProtocol or type(holdout_dataset) is not EvolutionDataset:
        raise ValueError("Retained typed holdout protocol and dataset are required")
    if type(holdout_protocol) is not EvaluationProtocol or type(training_dataset) is not EvolutionDataset:
        raise ValueError("Retained typed training protocol and dataset are required")
    if type(baseline) is not AgentGenome or type(challenger) is not AgentGenome:
        raise ValueError("Typed baseline and challenger genomes are required")
    require_protocol_genome(training_protocol, baseline)
    require_protocol_genome(training_protocol, challenger)
    if challenger.parent_ref != baseline.genome_ref:
        raise ValueError("Holdout evaluation requires a direct challenger of the baseline")
    require_genome_child(baseline, challenger)
    if training_protocol.dataset_ref != training_dataset.dataset_ref:
        raise ValueError("Training protocol and dataset identities differ")
    if holdout_protocol.dataset_ref != holdout_dataset.dataset_ref:
        raise ValueError("Holdout protocol and dataset identities differ")
    if (
        holdout_protocol.agent_id != training_protocol.agent_id
        or holdout_protocol.output_type != training_protocol.output_type
        or holdout_protocol.primary_metric != training_protocol.primary_metric
        or _criteria_fingerprint(holdout_protocol) != _criteria_fingerprint(training_protocol)
    ):
        raise ValueError("Holdout protocol must measure with the training protocol's criteria")
    if training_dataset.resource_class != "TRAINING":
        raise ValueError("Training comparison dataset must carry the TRAINING class")
    if holdout_dataset.resource_class != "HOLDOUT":
        raise ValueError("Independent evaluation accepts holdout datasets only")
    if holdout_dataset.provenance not in _HOLDOUT_PROVENANCE:
        raise ValueError("Synthetic development data cannot serve as a holdout set")
    if (
        holdout_dataset.agent_id != training_protocol.agent_id
        or training_dataset.agent_id != training_protocol.agent_id
    ):
        raise ValueError("Holdout and training datasets must belong to the protocol agent")
    training_inputs = {case.input_sha256 for case in training_dataset.cases}
    holdout_inputs = {case.input_sha256 for case in holdout_dataset.cases}
    training_ids = {case.case_id for case in training_dataset.cases}
    holdout_ids = {case.case_id for case in holdout_dataset.cases}
    if training_inputs & holdout_inputs or training_ids & holdout_ids:
        raise ValueError("Holdout cases overlap the training dataset; independence is unprovable")
    if any(row.method not in _DETERMINISTIC_METHODS for row in holdout_protocol.criteria):
        raise ValueError("Holdout protocol contains a metric without a deterministic adapter")

    case_count = len(holdout_dataset.cases)
    comparisons = []
    for criterion in holdout_protocol.criteria:
        metric = criterion.criterion.metric
        baseline_matches = _metric_matches(
            metric, holdout_protocol.agent_id, baseline_outputs, holdout_dataset
        )
        challenger_matches = _metric_matches(
            metric, holdout_protocol.agent_id, challenger_outputs, holdout_dataset
        )
        delta = Fraction(challenger_matches - baseline_matches, case_count)
        comparisons.append(
            MetricComparison(
                metric=metric,
                baseline_score=Decimal(baseline_matches) / Decimal(case_count),
                challenger_score=Decimal(challenger_matches) / Decimal(case_count),
                case_count=case_count,
                baseline_exact_matches=baseline_matches,
                challenger_exact_matches=challenger_matches,
                maximum_regression=criterion.maximum_regression,
                within_guardrail=delta >= -Fraction(criterion.maximum_regression),
            )
        )
    comparisons = tuple(comparisons)
    primary = next(
        row for row in holdout_protocol.criteria if row.criterion.metric == holdout_protocol.primary_metric
    )
    primary_comparison = next(
        row for row in comparisons if row.metric == holdout_protocol.primary_metric
    )
    exact_delta = Fraction(
        primary_comparison.challenger_exact_matches - primary_comparison.baseline_exact_matches,
        case_count,
    )
    improvement_met = exact_delta >= Fraction(primary.criterion.minimum_improvement) and exact_delta > 0
    passed = improvement_met and all(row.within_guardrail for row in comparisons)
    return HoldoutEvaluation(
        agent_id=holdout_protocol.agent_id,
        training_protocol_sha256=training_protocol.protocol_sha256,
        holdout_protocol_sha256=holdout_protocol.protocol_sha256,
        training_dataset_ref=training_dataset.dataset_ref,
        holdout_dataset_ref=holdout_dataset.dataset_ref,
        holdout_dataset_provenance=holdout_dataset.provenance,
        baseline_genome_ref=baseline.genome_ref,
        challenger_genome_ref=challenger.genome_ref,
        primary_metric=holdout_protocol.primary_metric,
        baseline_score=primary_comparison.baseline_score,
        challenger_score=primary_comparison.challenger_score,
        case_count=case_count,
        baseline_exact_matches=primary_comparison.baseline_exact_matches,
        challenger_exact_matches=primary_comparison.challenger_exact_matches,
        minimum_improvement=primary.criterion.minimum_improvement,
        minimum_improvement_met=improvement_met,
        metrics=comparisons,
        verdict="HOLDOUT_PASS" if passed else "HOLDOUT_FAIL",
        evaluated_at=evaluated_at,
    )

"""Agent confidence calibration against known local outcomes.

The classifier harness in ``calibration.py`` scores one fast-classifier
provider against labelled regime datasets. This module is the sibling
harness for *agent and research-pipeline confidence*: it takes
``(confidence, outcome)`` pairs whose outcomes are already known from
local evidence — Validation Fortress verdicts over the evaluated
hypothesis trials, and A02 regime assessments replayed against labelled
fixtures — and reports reliability by confidence bucket (bucketed
positive rate vs mean confidence, Brier score, expected calibration
error). Reports are descriptive evidence only: like
``calibration.CalibrationReport`` they confer no execution, promotion
or lifecycle authority, and a subject whose confidence never predicted
its outcome is reported exactly that way.

Two adapters bind real local artifacts:

- ``fortress_confidence_records`` reads an edge-sprint runner result
  JSON (the ``run_edge_sprint*.py`` summary format) and scores the
  pipeline's pre-holdout walk-forward signal
  (``walk_forward_positive_fraction`` — computed on in-sample folds
  before the untouched holdout is seen) against a chosen known outcome:
  the Fortress pass/fail verdict itself, or holdout profitability.
- ``regime_confidence_records`` consumes genuine A02
  ``RegimeDistribution`` output paired with known horizon labels and
  scores the top distribution score of each available horizon
  assessment against label agreement.
"""

import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Iterable, Literal, Mapping

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_quant_os.contracts import _require_aware
from ai_quant_os.regime_agent import RegimeDistribution

FortressOutcome = Literal["FORTRESS_PASS", "HOLDOUT_PROFITABLE"]


class ConfidenceRecord(BaseModel):
    """One scored prediction: a declared confidence and its known outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    sample_id: str = Field(min_length=1, max_length=256)
    subject_id: str = Field(min_length=1, max_length=128)
    subject_kind: Literal["AGENT", "PIPELINE"]
    confidence: Decimal = Field(ge=0, le=1)
    outcome: bool
    outcome_kind: str = Field(min_length=1, max_length=64)


class ReliabilityBucket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lower: Decimal
    upper: Decimal
    count: int
    mean_confidence: Decimal | None
    positive_rate: Decimal | None


class SubjectCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject_id: str
    subject_kind: Literal["AGENT", "PIPELINE"]
    count: int
    mean_confidence: Decimal
    positive_rate: Decimal


class AgentCalibrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"
    report_id: str
    dataset_ref: str = Field(min_length=1, max_length=512)
    outcome_kind: str = Field(min_length=1, max_length=64)
    created_at: AwareDatetime
    records: tuple[ConfidenceRecord, ...]
    record_count: int
    positive_count: int
    base_rate: Decimal
    brier_score: Decimal
    expected_calibration_error: Decimal
    reliability_buckets: tuple[ReliabilityBucket, ...]
    subjects: tuple[SubjectCalibration, ...]


def _content_ref(report: AgentCalibrationReport) -> str:
    payload = report.model_dump(mode="json", exclude={"report_id"})
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return "agent-calibration:" + digest


def build_agent_calibration_report(
    records: Iterable[ConfidenceRecord],
    *,
    dataset_ref: str,
    outcome_kind: str,
    created_at: datetime,
) -> AgentCalibrationReport:
    """Build a reliability report over confidence/outcome records.

    All records must share ``outcome_kind`` — mixing outcomes (a Fortress
    pass is not a profitable holdout) would silently corrupt the rates.
    """
    _require_aware(created_at)
    if not 0 < len(dataset_ref) <= 512 or not 0 < len(outcome_kind) <= 64:
        raise ValueError("Dataset reference and outcome kind are required and bounded")
    records = tuple(sorted(records, key=lambda row: row.sample_id))
    if not records or len(records) > 100000:
        raise ValueError("Calibration requires 1-100000 records")
    if len({row.sample_id for row in records}) != len(records):
        raise ValueError("Duplicate calibration sample IDs")
    if {row.outcome_kind for row in records} != {outcome_kind}:
        raise ValueError("Calibration records mix outcome kinds")
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        positives = sum(row.outcome for row in records)
        brier = sum(
            ((row.confidence - Decimal(row.outcome)) ** 2 for row in records), Decimal(0)
        ) / len(records)
        buckets = []
        ece = Decimal(0)
        for index in range(10):
            members = tuple(row for row in records if min(int(row.confidence * 10), 9) == index)
            mean = (
                sum((row.confidence for row in members), Decimal(0)) / len(members)
                if members
                else None
            )
            rate = Decimal(sum(row.outcome for row in members)) / len(members) if members else None
            if members:
                ece += Decimal(len(members)) / len(records) * abs(mean - rate)
            buckets.append(
                ReliabilityBucket(
                    lower=Decimal(index) / 10,
                    upper=Decimal(index + 1) / 10,
                    count=len(members),
                    mean_confidence=mean,
                    positive_rate=rate,
                )
            )
        subjects = []
        for key in sorted({(row.subject_id, row.subject_kind) for row in records}):
            members = tuple(row for row in records if (row.subject_id, row.subject_kind) == key)
            subjects.append(
                SubjectCalibration(
                    subject_id=key[0],
                    subject_kind=key[1],
                    count=len(members),
                    mean_confidence=sum((row.confidence for row in members), Decimal(0))
                    / len(members),
                    positive_rate=Decimal(sum(row.outcome for row in members)) / len(members),
                )
            )
        data = dict(
            dataset_ref=dataset_ref,
            outcome_kind=outcome_kind,
            created_at=created_at,
            records=records,
            record_count=len(records),
            positive_count=positives,
            base_rate=Decimal(positives) / len(records),
            brier_score=brier,
            expected_calibration_error=ece,
            reliability_buckets=tuple(buckets),
            subjects=tuple(subjects),
        )
    draft = AgentCalibrationReport(report_id="pending", **data)
    return draft.model_copy(update={"report_id": _content_ref(draft)})


def verify_agent_calibration_report(report: AgentCalibrationReport) -> None:
    expected = build_agent_calibration_report(
        report.records,
        dataset_ref=report.dataset_ref,
        outcome_kind=report.outcome_kind,
        created_at=report.created_at,
    )
    if report != expected:
        raise ValueError("Agent calibration report integrity verification failed")


def _sprint_evaluations(text: str) -> list[dict]:
    """Extract the evaluations list from a sprint runner result artifact.

    Artifacts on disk are either the bare summary JSON or the runner's
    full stdout with the JSON between ``===JSON===`` and ``===END===``
    markers; both forms are accepted so historical artifacts stay
    readable.
    """
    raw = text
    if "===JSON===" in raw:
        raw = raw.split("===JSON===", 1)[1]
    if "===END===" in raw:
        raw = raw.split("===END===", 1)[0]
    payload = json.loads(raw)
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list) or not evaluations:
        raise ValueError("Sprint artifact carries no evaluations")
    return evaluations


def fortress_confidence_records(
    text: str, *, source_ref: str, outcome_kind: FortressOutcome
) -> tuple[ConfidenceRecord, ...]:
    """Score the research pipeline's walk-forward signal against outcomes.

    The confidence is the trial's ``walk_forward_positive_fraction``:
    the fraction of in-sample walk-forward folds that were positive,
    computed before the untouched holdout is evaluated — the same
    pre-verdict signal the pipeline itself surfaces for every trial.
    ``outcome_kind`` selects the known outcome each trial is scored
    against: the Fortress verdict (``FORTRESS_PASS``) or whether the
    untouched holdout was profitable (``HOLDOUT_PROFITABLE``).
    """
    if not source_ref or len(source_ref) > 128:
        raise ValueError("Sprint source reference is required and bounded")
    records = []
    for row in _sprint_evaluations(text):
        metrics = row["metrics"]
        if outcome_kind == "FORTRESS_PASS":
            outcome = bool(row["passed"])
        else:
            outcome = Decimal(str(metrics["holdout_total_return"])) > 0
        records.append(
            ConfidenceRecord(
                sample_id=f"{source_ref}:{row['hypothesis']}:{row['instrument']}",
                subject_id=row["hypothesis"],
                subject_kind="PIPELINE",
                confidence=Decimal(str(metrics["walk_forward_positive_fraction"])),
                outcome=outcome,
                outcome_kind=outcome_kind,
            )
        )
    return tuple(records)


def regime_confidence_records(
    output: RegimeDistribution,
    expected: Mapping[tuple[str, str], str],
    *,
    source_ref: str,
) -> tuple[ConfidenceRecord, ...]:
    """Score A02's horizon distributions against known regime labels.

    ``expected`` maps ``(instrument_id, horizon)`` to the known label.
    Only AVAILABLE assessments that actually predicted (a non-UNKNOWN
    top label) become records; unavailable or abstaining horizons are
    not predictions and are skipped, never scored as failures. The
    confidence is the distribution score of the predicted label — the
    quantity A02 itself declares (``UNCALIBRATED_FEATURE_SCORES``),
    which is exactly what this harness exists to measure.
    """
    if not source_ref or len(source_ref) > 128:
        raise ValueError("Regime source reference is required and bounded")
    records = []
    for instrument in output.assessments:
        for assessment in instrument.horizons:
            label = expected.get((instrument.instrument_id, assessment.horizon))
            if label is None or assessment.status != "AVAILABLE":
                continue
            predicted, score = max(assessment.distribution.items(), key=lambda item: item[1])
            if predicted == "UNKNOWN":
                continue
            records.append(
                ConfidenceRecord(
                    sample_id=(f"{source_ref}:{instrument.instrument_id}:{assessment.horizon}"),
                    subject_id="A02",
                    subject_kind="AGENT",
                    confidence=score,
                    outcome=predicted == label,
                    outcome_kind="REGIME_LABEL_MATCH",
                )
            )
    return tuple(records)

"""Descriptive classifier evaluation; reports confer no execution authority."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.contracts import _require_aware
from ai_quant_os.decision_gateway import Classification

KnownClassification = Literal["TRENDING", "RANGING", "HIGH_VOLATILITY"]
Outcome = Literal["VALID", "ABSTAIN", "TIMEOUT", "FAILED", "INVALID", "EXPIRED"]


class EvaluationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    sample_id: str = Field(min_length=1, max_length=256)
    input_ref: str = Field(min_length=1, max_length=512)
    label_ref: str = Field(min_length=1, max_length=512)
    instrument_id: str = Field(min_length=1, max_length=32)
    expected: KnownClassification
    predicted: Classification | None
    confidence: Decimal | None = Field(ge=0, le=1)
    outcome: Outcome
    latency_ms: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def consistent_outcome(self) -> "EvaluationRecord":
        if self.outcome == "VALID":
            if self.predicted in (None, "UNKNOWN") or self.confidence is None:
                raise ValueError("Valid predictions require classification and confidence")
        elif self.outcome == "ABSTAIN":
            if self.predicted != "UNKNOWN" or self.confidence is None:
                raise ValueError("Abstention must carry UNKNOWN and declared confidence")
        elif self.predicted is not None or self.confidence is not None:
            raise ValueError("Failed evaluations cannot carry a primary prediction")
        return self


class EvaluationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=128)
    dataset_ref: str = Field(min_length=1, max_length=512)
    records: tuple[EvaluationRecord, ...] = Field(min_length=1, max_length=10000)


class ReliabilityBin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lower: Decimal
    upper: Decimal
    count: int
    mean_confidence: Decimal | None
    accuracy: Decimal | None


class CalibrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"
    report_id: str
    provider_id: str
    provider_version: str
    dataset_ref: str
    created_at: AwareDatetime
    records: tuple[EvaluationRecord, ...]
    attempt_count: int
    valid_count: int
    abstain_count: int
    failure_count: int
    coverage: Decimal
    accuracy: Decimal | None
    correct_fraction_of_attempts: Decimal
    top_label_brier: Decimal | None
    expected_calibration_error: Decimal | None
    mean_latency_ms: Decimal
    p95_latency_ms: Decimal
    reliability_bins: tuple[ReliabilityBin, ...]


def build_calibration_report(
    records: tuple[EvaluationRecord, ...],
    *,
    provider_id: str,
    provider_version: str,
    dataset_ref: str,
    created_at: datetime,
) -> CalibrationReport:
    _require_aware(created_at)
    if not records or len(records) > 100000:
        raise ValueError("Evaluation requires 1-100000 records")
    if not all(0 < len(value) <= 512 for value in (provider_id, provider_version, dataset_ref)):
        raise ValueError("Provider and dataset references are required and bounded")
    records = tuple(sorted(records, key=lambda row: row.sample_id))
    if len({row.sample_id for row in records}) != len(records):
        raise ValueError("Duplicate evaluation sample IDs")
    if len({(row.input_ref, row.instrument_id) for row in records}) != len(records):
        raise ValueError("Duplicate evaluation inputs")
    valid = tuple(row for row in records if row.outcome == "VALID")
    correct = sum(row.predicted == row.expected for row in valid)
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        bins = []
        ece = Decimal(0)
        for index in range(10):
            members = tuple(row for row in valid if min(int(row.confidence * 10), 9) == index)
            mean = (
                sum((row.confidence for row in members), Decimal(0)) / len(members)
                if members
                else None
            )
            accuracy = (
                Decimal(sum(row.predicted == row.expected for row in members)) / len(members)
                if members
                else None
            )
            if members:
                ece += Decimal(len(members)) / len(valid) * abs(mean - accuracy)
            bins.append(
                ReliabilityBin(
                    lower=Decimal(index) / 10,
                    upper=Decimal(index + 1) / 10,
                    count=len(members),
                    mean_confidence=mean,
                    accuracy=accuracy,
                )
            )
        latencies = sorted(row.latency_ms for row in records)
        data = dict(
            provider_id=provider_id,
            provider_version=provider_version,
            dataset_ref=dataset_ref,
            created_at=created_at,
            records=records,
            attempt_count=len(records),
            valid_count=len(valid),
            abstain_count=sum(row.outcome == "ABSTAIN" for row in records),
            failure_count=sum(row.outcome not in ("VALID", "ABSTAIN") for row in records),
            coverage=Decimal(len(valid)) / len(records),
            accuracy=Decimal(correct) / len(valid) if valid else None,
            correct_fraction_of_attempts=Decimal(correct) / len(records),
            top_label_brier=sum(
                ((row.confidence - Decimal(row.predicted == row.expected)) ** 2 for row in valid),
                Decimal(0),
            )
            / len(valid)
            if valid
            else None,
            expected_calibration_error=ece if valid else None,
            mean_latency_ms=sum(latencies, Decimal(0)) / len(latencies),
            p95_latency_ms=latencies[(95 * len(latencies) + 99) // 100 - 1],
            reliability_bins=tuple(bins),
        )
    draft = CalibrationReport(report_id="pending", **data)
    payload = draft.model_dump(mode="json", exclude={"report_id"})
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return draft.model_copy(update={"report_id": "calibration:" + digest})


def verify_calibration_report(report: CalibrationReport) -> None:
    expected = build_calibration_report(
        report.records,
        provider_id=report.provider_id,
        provider_version=report.provider_version,
        dataset_ref=report.dataset_ref,
        created_at=report.created_at,
    )
    if report != expected:
        raise ValueError("Calibration report integrity verification failed")

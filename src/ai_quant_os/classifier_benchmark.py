"""Replay classifier evaluations through the same validation path as the paper API."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from time import perf_counter

from ai_quant_os.contracts import _require_aware
from ai_quant_os.calibration import (
    CalibrationReport,
    EvaluationRecord,
    KnownClassification,
    build_calibration_report,
)
from ai_quant_os.decision_gateway import DecisionGateway, FastClassifier
from ai_quant_os.world_state import WorldState


@dataclass(frozen=True)
class BenchmarkCase:
    sample_id: str
    snapshot: WorldState
    instrument_id: str
    expected: KnownClassification
    label_ref: str

    def __post_init__(self) -> None:
        _require_aware(self.snapshot.as_of)
        _require_aware(self.snapshot.valid_until)
        if not 1 <= len(self.sample_id) <= 128 or not 1 <= len(self.label_ref) <= 512:
            raise ValueError("Invalid benchmark case identity or label reference")
        if self.expected not in ("TRENDING", "RANGING", "HIGH_VOLATILITY"):
            raise ValueError("Invalid held-out benchmark label")
        if self.instrument_id not in {item.instrument_id for item in self.snapshot.instruments}:
            raise ValueError("Unknown benchmark instrument")


async def run_benchmark(
    cases: tuple[BenchmarkCase, ...],
    *,
    provider: FastClassifier,
    dataset_ref: str,
    created_at: datetime,
    timeout_seconds: float = 0.2,
) -> CalibrationReport:
    if not cases or len(cases) > 10000:
        raise ValueError("Benchmark requires 1-10000 cases")
    if len({case.sample_id for case in cases}) != len(cases):
        raise ValueError("Duplicate benchmark sample IDs")
    if len({(case.snapshot.world_state_version, case.instrument_id) for case in cases}) != len(
        cases
    ):
        raise ValueError("Duplicate benchmark inputs")
    records = []
    for case in cases:
        started = perf_counter()
        gateway = DecisionGateway(
            primary=provider,
            timeout_seconds=timeout_seconds,
            clock=lambda: case.snapshot.as_of + timedelta(seconds=perf_counter() - started),
        )
        predicted = confidence = None
        try:
            envelope = await gateway.classify(
                case.snapshot, instrument_id=case.instrument_id, task_id=case.sample_id
            )
        except ValueError:
            outcome = "EXPIRED"
        else:
            reasons = envelope.output.fallback_reasons
            if "PRIMARY_TIMEOUT" in reasons:
                outcome = "TIMEOUT"
            elif "PRIMARY_FAILED" in reasons:
                outcome = "FAILED"
            elif "PRIMARY_INVALID" in reasons or envelope.model_route != provider.provider_id:
                outcome = "INVALID"
            else:
                predicted = envelope.output.classification
                confidence = envelope.confidence
                outcome = "ABSTAIN" if predicted == "UNKNOWN" else "VALID"
        records.append(
            EvaluationRecord(
                sample_id=case.sample_id,
                input_ref=case.snapshot.world_state_version,
                label_ref=case.label_ref,
                instrument_id=case.instrument_id,
                expected=case.expected,
                predicted=predicted,
                confidence=confidence,
                outcome=outcome,
                latency_ms=Decimal(str((perf_counter() - started) * 1000)),
            )
        )
    return build_calibration_report(
        tuple(records),
        provider_id=provider.provider_id,
        provider_version=provider.provider_version,
        dataset_ref=dataset_ref,
        created_at=created_at,
    )

"""Agent confidence calibration harness: exact math, adapters, integrity."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ai_quant_os.agent_calibration import (
    ConfidenceRecord,
    build_agent_calibration_report,
    fortress_confidence_records,
    regime_confidence_records,
    verify_agent_calibration_report,
)
from ai_quant_os.regime_agent import HorizonAssessment, InstrumentAssessment, RegimeDistribution

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(sample, subject, confidence, outcome, kind="HOLDOUT_PROFITABLE"):
    return ConfidenceRecord(
        sample_id=sample,
        subject_id=subject,
        subject_kind="PIPELINE",
        confidence=Decimal(confidence),
        outcome=outcome,
        outcome_kind=kind,
    )


def test_report_math_is_exact():
    records = (
        record("s1", "S01", "0.9", True),
        record("s2", "S01", "0.8", False),
        record("s3", "S02", "0.2", False),
        record("s4", "S02", "0.1", True),
    )
    report = build_agent_calibration_report(
        records, dataset_ref="unit", outcome_kind="HOLDOUT_PROFITABLE", created_at=NOW
    )
    assert report.record_count == 4
    assert report.positive_count == 2
    assert report.base_rate == Decimal("0.5")
    # ((0.9-1)^2 + 0.8^2 + 0.2^2 + (0.1-1)^2) / 4 = 1.50 / 4
    assert report.brier_score == Decimal("0.375")
    # bucket contributions: .1/4 + .8/4 + .2/4 + .9/4
    assert report.expected_calibration_error == Decimal("0.5")
    bucket9 = report.reliability_buckets[9]
    assert (bucket9.count, bucket9.mean_confidence, bucket9.positive_rate) == (
        1,
        Decimal("0.9"),
        Decimal(1),
    )
    bucket0 = report.reliability_buckets[0]
    assert (bucket0.count, bucket0.mean_confidence, bucket0.positive_rate) == (0, None, None)
    subjects = {row.subject_id: row for row in report.subjects}
    assert subjects["S01"].positive_rate == Decimal("0.5")
    assert subjects["S02"].mean_confidence == Decimal("0.15")
    verify_agent_calibration_report(report)


def test_report_rejects_duplicates_and_mixed_outcomes():
    with pytest.raises(ValueError):
        build_agent_calibration_report(
            (record("s1", "S01", "0.5", True), record("s1", "S01", "0.5", False)),
            dataset_ref="unit",
            outcome_kind="HOLDOUT_PROFITABLE",
            created_at=NOW,
        )
    with pytest.raises(ValueError):
        build_agent_calibration_report(
            (
                record("s1", "S01", "0.5", True),
                record("s2", "S01", "0.5", True, kind="FORTRESS_PASS"),
            ),
            dataset_ref="unit",
            outcome_kind="HOLDOUT_PROFITABLE",
            created_at=NOW,
        )


def test_report_integrity_detects_tampering():
    report = build_agent_calibration_report(
        (record("s1", "S01", "0.5", True),),
        dataset_ref="unit",
        outcome_kind="HOLDOUT_PROFITABLE",
        created_at=NOW,
    )
    tampered = report.model_copy(update={"positive_count": 0})
    with pytest.raises(ValueError):
        verify_agent_calibration_report(tampered)


SPRINT_JSON = json.dumps(
    {
        "evaluations": [
            {
                "hypothesis": "S06",
                "instrument": "EURJPY",
                "passed": False,
                "metrics": {
                    "walk_forward_positive_fraction": 0.8,
                    "holdout_total_return": 0.05,
                },
            },
            {
                "hypothesis": "S06",
                "instrument": "USDJPY",
                "passed": False,
                "metrics": {
                    "walk_forward_positive_fraction": 0.6,
                    "holdout_total_return": -0.02,
                },
            },
            {
                "hypothesis": "I01",
                "instrument": "USDINR",
                "passed": True,
                "metrics": {
                    "walk_forward_positive_fraction": 0.6,
                    "holdout_total_return": 0.07,
                },
            },
        ]
    }
)


def test_fortress_adapter_scores_pre_holdout_signal():
    passed = fortress_confidence_records(
        SPRINT_JSON, source_ref="sprintX", outcome_kind="FORTRESS_PASS"
    )
    assert [row.outcome for row in passed] == [False, False, True]
    assert passed[0].confidence == Decimal("0.8")
    assert passed[0].subject_id == "S06" and passed[0].subject_kind == "PIPELINE"
    holdout = fortress_confidence_records(
        SPRINT_JSON, source_ref="sprintX", outcome_kind="HOLDOUT_PROFITABLE"
    )
    assert [row.outcome for row in holdout] == [True, False, True]
    report = build_agent_calibration_report(
        holdout, dataset_ref="sprintX", outcome_kind="HOLDOUT_PROFITABLE", created_at=NOW
    )
    assert report.record_count == 3 and report.positive_count == 2


def test_fortress_adapter_accepts_wrapped_runner_output():
    wrapped = "doc text\n===JSON===\n" + SPRINT_JSON + "\n===END===\n"
    records = fortress_confidence_records(
        wrapped, source_ref="sprintY", outcome_kind="FORTRESS_PASS"
    )
    assert len(records) == 3
    assert records[0].sample_id.startswith("sprintY:")


def _assessment(horizon, status, distribution):
    return HorizonAssessment(
        horizon=horizon,
        status=status,
        bar_count=8 if status == "AVAILABLE" else 0,
        observed_span_seconds=Decimal(7) if status == "AVAILABLE" else None,
        regime=max(distribution, key=distribution.get) if status == "AVAILABLE" else "UNKNOWN",
        distribution=distribution,
    )


def _distribution(**overrides):
    base = {
        "TRENDING": Decimal("0.7"),
        "RANGING": Decimal("0.2"),
        "HIGH_VOLATILITY": Decimal("0.05"),
        "UNKNOWN": Decimal("0.05"),
    }
    base.update(overrides)
    return base


def test_regime_adapter_scores_only_real_predictions():
    output = RegimeDistribution(
        task_id="t1",
        input_refs=("ref:a",),
        evidence_refs=("ref:a",),
        confidence=Decimal(0),
        uncertainty=Decimal(1),
        created_at=NOW,
        valid_until=NOW.replace(second=30),
        contradictions=(),
        trace_id="trace-1",
        assessments=(
            InstrumentAssessment(
                instrument_id="EURUSD",
                horizons=(
                    _assessment("SHORT", "AVAILABLE", _distribution()),
                    _assessment(
                        "MEDIUM",
                        "AVAILABLE",
                        _distribution(
                            TRENDING=Decimal("0.1"),
                            RANGING=Decimal("0.1"),
                            HIGH_VOLATILITY=Decimal("0.1"),
                            UNKNOWN=Decimal("0.7"),
                        ),
                    ),
                    _assessment("LONG", "INSUFFICIENT", _distribution()),
                ),
            ),
        ),
    )
    expected = {
        ("EURUSD", "SHORT"): "TRENDING",
        ("EURUSD", "MEDIUM"): "RANGING",
        ("EURUSD", "LONG"): "TRENDING",
    }
    records = regime_confidence_records(output, expected, source_ref="fixture")
    # MEDIUM abstains (UNKNOWN top), LONG is not AVAILABLE: only SHORT scores.
    assert len(records) == 1
    assert records[0].confidence == Decimal("0.7")
    assert records[0].outcome is True
    assert records[0].subject_id == "A02" and records[0].subject_kind == "AGENT"


def test_regime_adapter_against_genuine_a02_graph_output():
    """Run the real A02 graph over the labelled fixture and calibrate it."""
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
    from ai_quant_os.graph_runtime import GraphRuntime
    from ai_quant_os.market import MarketSnapshot
    from ai_quant_os.market_data import Bar
    from ai_quant_os.paper_context_sources import paper_context_sources
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.regime_agent import regime_implementation
    from ai_quant_os.regime_evaluation import Fixture
    from ai_quant_os.world_state import WorldStateEngine

    fixture_path = Path(__file__).parent / "fixtures" / "regime_baseline.json"
    fixture = Fixture.model_validate_json(fixture_path.read_bytes())
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    registry = load_agent_catalog()

    async def run_case(case, runtime):
        closes = tuple(
            value
            for segment in case.segments
            for value in (
                (segment.start if i % 2 == 0 else segment.alternate)
                if segment.alternate is not None
                else segment.start + segment.step * i
                for i in range(segment.count)
            )
        )
        bars = tuple(
            Bar(
                "EURUSD",
                now - timedelta(seconds=len(closes) - i - 1),
                close,
                close,
                close,
                close,
                Decimal(1),
            )
            for i, close in enumerate(closes)
        )
        engine = WorldStateEngine(PAPER_INSTRUMENTS)
        engine.record_bars(bars, now=now)
        snapshot = engine.snapshot(
            (
                MarketSnapshot(
                    "EURUSD",
                    closes[-1],
                    closes[-1] + Decimal("0.0001"),
                    now,
                    "synthetic-eval",
                    True,
                ),
            ),
            now=now,
        )
        context = await ContextCompiler(
            registry, paper_context_sources(snapshot, engine.histories()), clock=lambda: now
        ).compile("A02", "0.2.0", task_id=case.id, scope="PAPER")
        receipt = await runtime.run(case.id, context)
        assert receipt.output_json is not None
        return RegimeDistribution.model_validate_json(receipt.output_json)

    async def collect(tmp):
        async with GraphRuntime.open(
            tmp / "graph.sqlite3",
            registry=registry,
            implementations={("A02", "0.2.0"): regime_implementation(registry)},
            tools=AgentToolBoundary(registry, {}),
            clock=lambda: now,
        ) as runtime:
            records = []
            for case in fixture.cases:
                output = await run_case(case, runtime)
                expected = {
                    ("EURUSD", horizon): label
                    for horizon, label in zip(
                        ("SHORT", "MEDIUM", "LONG"), case.expected, strict=True
                    )
                }
                records.extend(regime_confidence_records(output, expected, source_ref=case.id))
            return tuple(records)

    import tempfile

    with tempfile.TemporaryDirectory(prefix="ai-quant-calibration-") as directory:
        records = asyncio.run(collect(Path(directory)))
    assert records, "fixture produced no scored A02 predictions"
    report = build_agent_calibration_report(
        records,
        dataset_ref="tests/fixtures/regime_baseline.json (SYNTHETIC_DEVELOPMENT_BASELINE_NOT_HOLDOUT)",
        outcome_kind="REGIME_LABEL_MATCH",
        created_at=now,
    )
    verify_agent_calibration_report(report)
    assert report.subjects[0].subject_id == "A02"
    assert 0 <= report.base_rate <= 1

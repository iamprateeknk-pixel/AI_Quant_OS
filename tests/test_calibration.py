import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

import pytest

from ai_quant_os.calibration import EvaluationRecord, build_calibration_report
from ai_quant_os.calibration_store import CalibrationStore
from ai_quant_os.classifier_benchmark import BenchmarkCase, run_benchmark
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(
    sample,
    expected="RANGING",
    prediction="RANGING",
    confidence="0.75",
    outcome="VALID",
    latency="1",
):
    return EvaluationRecord(
        sample_id=sample,
        input_ref="world:" + sample,
        label_ref="labels:" + sample,
        instrument_id="EURUSD",
        expected=expected,
        predicted=prediction,
        confidence=confidence,
        outcome=outcome,
        latency_ms=latency,
    )


def report():
    rows = (
        record("a"),
        record("b", prediction="TRENDING", latency="2"),
        record("c", prediction=None, confidence=None, outcome="TIMEOUT", latency="10"),
        record("d", prediction="UNKNOWN", confidence="0.1", outcome="ABSTAIN", latency="3"),
    )
    return build_calibration_report(
        rows, provider_id="FIXTURE", provider_version="1", dataset_ref="heldout:1", created_at=NOW
    )


def test_report_metrics_include_failures_and_do_not_treat_abstentions_as_predictions():
    result = report()
    assert result.attempt_count == 4
    assert result.valid_count == 2
    assert result.abstain_count == 1
    assert result.failure_count == 1
    assert result.coverage == Decimal("0.5")
    assert result.accuracy == Decimal("0.5")
    assert result.correct_fraction_of_attempts == Decimal("0.25")
    assert result.top_label_brier == Decimal("0.3125")
    assert result.expected_calibration_error == Decimal("0.25")
    assert result.mean_latency_ms == Decimal("4")
    assert result.p95_latency_ms == Decimal("10")
    bucket = next(item for item in result.reliability_bins if item.count)
    assert bucket.mean_confidence == Decimal("0.75")
    assert bucket.accuracy == Decimal("0.5")
    assert result.status == "EVIDENCE_ONLY"


def test_report_fingerprint_is_stable_under_record_order_and_changes_with_evidence():
    result = report()
    reversed_report = build_calibration_report(
        tuple(reversed(result.records)),
        provider_id="FIXTURE",
        provider_version="1",
        dataset_ref="heldout:1",
        created_at=NOW,
    )
    assert result == reversed_report
    changed = build_calibration_report(
        result.records,
        provider_id="FIXTURE",
        provider_version="2",
        dataset_ref="heldout:1",
        created_at=NOW,
    )
    assert changed.report_id != result.report_id
    with pytest.raises(ValueError, match="Duplicate"):
        build_calibration_report(
            (result.records[0], result.records[0]),
            provider_id="FIXTURE",
            provider_version="1",
            dataset_ref="heldout:1",
            created_at=NOW,
        )


def test_report_handles_zero_and_one_confidence_and_all_failure_batches():
    result = build_calibration_report(
        (record("zero", confidence="0"), record("one", confidence="1")),
        provider_id="FIXTURE",
        provider_version="1",
        dataset_ref="heldout:1",
        created_at=NOW,
    )
    assert result.reliability_bins[0].count == 1
    assert result.reliability_bins[-1].count == 1
    failed = build_calibration_report(
        (record("timeout", prediction=None, confidence=None, outcome="TIMEOUT"),),
        provider_id="FIXTURE",
        provider_version="1",
        dataset_ref="heldout:1",
        created_at=NOW,
    )
    assert failed.accuracy is None
    assert failed.expected_calibration_error is None
    assert failed.coverage == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": "NaN"},
        {"outcome": "VALID", "predicted": None},
        {"outcome": "FAILED", "predicted": "RANGING"},
        {"latency_ms": "-1"},
        {"approved_exposure": "100"},
    ],
)
def test_evaluation_schema_rejects_invalid_or_authority_fields(changes):
    payload = record("a").model_dump()
    with pytest.raises(ValueError):
        EvaluationRecord.model_validate({**payload, **changes})


def test_store_survives_restart_and_rejects_tampered_reports(tmp_path):
    path = tmp_path / "calibration.db"
    store = CalibrationStore(path)
    result = report()
    assert store.put(result) is True
    assert store.put(result) is False
    assert CalibrationStore(path).get(result.report_id) == result
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE calibration_reports SET report_json = '{}' WHERE report_id = ?",
            (result.report_id,),
        )
    with pytest.raises(ValueError, match="integrity"):
        store.get(result.report_id)


def test_benchmark_hides_labels_and_tracks_primary_failures():
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    snapshot = engine.snapshot(
        (MarketSnapshot("EURUSD", Decimal("1.1"), Decimal("1.1001"), NOW, "fixture", True),),
        now=NOW,
    )
    case = BenchmarkCase(
        sample_id="case",
        snapshot=snapshot,
        instrument_id="EURUSD",
        expected="RANGING",
        label_ref="heldout:label",
    )

    class FailingProvider:
        provider_id = "FIXTURE"
        provider_version = "1"

        async def classify(self, world, instrument_id):
            assert not hasattr(world, "expected")
            raise RuntimeError("private provider failure")

    result = asyncio.run(
        run_benchmark((case,), provider=FailingProvider(), dataset_ref="heldout:1", created_at=NOW)
    )
    assert result.failure_count == 1
    assert result.records[0].outcome == "FAILED"
    assert result.records[0].predicted is None
    assert "private" not in result.model_dump_json()


def test_api_report_creation_is_audited_persisted_and_never_clears_escalation(tmp_path):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app

    path = tmp_path / "paper.db"
    client = TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, paper_db_path=path)
    )
    payload = {
        "provider_id": "FIXTURE",
        "provider_version": "1",
        "dataset_ref": "heldout:1",
        "records": [row.model_dump(mode="json") for row in report().records],
    }
    url = "/api/v1/paper/decisions/calibration"
    assert client.post(url, json=payload).status_code == 401
    headers = {"X-Operator-Token": "fixture-token"}
    response = client.post(url, json=payload, headers=headers)
    assert response.status_code == 200
    saved = response.json()
    assert saved["status"] == "EVIDENCE_ONLY"
    restarted = TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, paper_db_path=path)
    )
    assert restarted.get("/api/v1/calibration/" + saved["report_id"]).json() == saved
    assert restarted.get("/api/v1/calibration/missing").status_code == 404
    assert restarted.get("/api/v1/audit").json()["events"][-1]["report_id"] == saved["report_id"]
    classification = restarted.post(
        "/api/v1/paper/decisions/classify",
        headers=headers,
        json={"instrument_id": "EURUSD", "task_id": "task"},
    ).json()
    assert classification["requires_escalation"] is True
    assert restarted.get("/api/v1/orders").json()["orders"] == []
    assert (
        restarted.post(
            url, json={**payload, "approved_by_policy": True}, headers=headers
        ).status_code
        == 422
    )


def test_metrics_cannot_be_overridden_before_store_write(tmp_path):
    store = CalibrationStore(tmp_path / "reports.db")
    altered = report().model_copy(update={"accuracy": Decimal(1)})
    with pytest.raises(ValueError, match="integrity"):
        store.put(altered)


def test_benchmark_scores_valid_primary_and_marks_abstention():
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    snapshots = []
    for index in range(2):
        now = NOW + timedelta(seconds=index)
        snapshots.append(
            engine.snapshot(
                (
                    MarketSnapshot(
                        "EURUSD", Decimal("1.1"), Decimal("1.1001"), now, "fixture", True
                    ),
                ),
                now=now,
            )
        )
    cases = tuple(
        BenchmarkCase(
            sample_id=str(index),
            snapshot=snapshot,
            instrument_id="EURUSD",
            expected="RANGING",
            label_ref="labels:" + str(index),
        )
        for index, snapshot in enumerate(snapshots)
    )

    class FixtureProvider:
        provider_id = "FIXTURE"
        provider_version = "1"

        async def classify(self, snapshot, instrument_id):
            refs = snapshot.instruments[0].evidence_refs
            return {
                "schema_version": "1.0",
                "world_state_version": snapshot.world_state_version,
                "instrument_id": instrument_id,
                "classification": "RANGING" if snapshot.as_of == NOW else "UNKNOWN",
                "confidence": "0.75",
                "created_at": snapshot.as_of,
                "expires_at": snapshot.valid_until,
                "evidence_refs": refs,
                "calibration_ref": None,
            }

    result = asyncio.run(
        run_benchmark(cases, provider=FixtureProvider(), dataset_ref="heldout:1", created_at=NOW)
    )
    assert result.valid_count == 1
    assert result.accuracy == 1
    assert result.abstain_count == 1
    assert result.coverage == Decimal("0.5")


def test_benchmark_rejects_invalid_cases_before_provider_calls():
    snapshot = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    with pytest.raises(ValueError):
        BenchmarkCase(
            sample_id="case",
            snapshot=snapshot,
            instrument_id="UNKNOWN",
            expected="RANGING",
            label_ref="labels:1",
        )
    with pytest.raises(ValueError):
        BenchmarkCase(
            sample_id="case",
            snapshot=snapshot,
            instrument_id="EURUSD",
            expected="BUY",
            label_ref="labels:1",
        )


def test_cli_creates_and_verifies_report_without_overwriting_or_echoing_invalid_input(tmp_path):
    import json
    import subprocess
    import sys
    from ai_quant_os.calibration import CalibrationReport

    incoming = tmp_path / "observations.json"
    outgoing = tmp_path / "report.json"
    database = tmp_path / "reports.db"
    payload = {
        "provider_id": "FIXTURE",
        "provider_version": "1",
        "dataset_ref": "heldout:1",
        "records": [row.model_dump(mode="json") for row in report().records],
    }
    incoming.write_text(json.dumps(payload))
    args = [
        sys.executable,
        "-m",
        "ai_quant_os.evaluation_cli",
        "report",
        str(incoming),
        "--output",
        str(outgoing),
        "--database",
        str(database),
    ]
    created = subprocess.run(args, capture_output=True, text=True)
    assert created.returncode == 0, created.stderr
    artifact = CalibrationReport.model_validate_json(outgoing.read_bytes())
    assert CalibrationStore(database).get(artifact.report_id) == artifact
    verified = subprocess.run(
        [sys.executable, "-m", "ai_quant_os.evaluation_cli", "verify", str(outgoing)],
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0
    before = outgoing.read_bytes()
    assert subprocess.run(args, capture_output=True, text=True).returncode == 2
    assert outgoing.read_bytes() == before
    incoming.write_text(json.dumps({**payload, "private_key": "DO_NOT_ECHO_FIXTURE"}))
    failed = subprocess.run(args, capture_output=True, text=True)
    assert failed.returncode == 2
    assert "DO_NOT_ECHO_FIXTURE" not in failed.stdout + failed.stderr

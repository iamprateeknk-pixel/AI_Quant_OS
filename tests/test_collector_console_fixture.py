"""Frozen console data equals a protected read of original native collector bytes."""

from datetime import datetime
import json
from pathlib import Path

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.paper_telemetry import PaperTelemetry
from ai_quant_os.collector_trace_api import CollectorTraceObservation


def test_console_fixture_matches_original_native_file_and_protected_api():
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads(
        (root / "apps/web/tests/fixtures/collector-trace.json").read_text(encoding="utf-8")
    )
    CollectorTraceObservation.model_validate(fixture)
    source = root / "tests/fixtures/collector-trace-native.jsonl"
    with TestClient(
        create_app(
            operator_token="fixture-token",
            telemetry=PaperTelemetry(),
            collector_trace_file=source,
            clock=lambda: datetime.fromisoformat(fixture["observed_at"].replace("Z", "+00:00")),
        )
    ) as client:
        response = client.get(
            "/api/v1/observability/traces/" + fixture["trace_id"],
            headers={"X-Operator-Token": "fixture-token"},
        )
        assert response.status_code == 200 and response.json() == fixture

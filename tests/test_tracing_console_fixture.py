"""Console fixtures retain actual protected API and native SDK accounting."""

from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.api import create_app
from ai_quant_os.paper_telemetry import PaperTelemetry
from ai_quant_os.tracing_status_api import TracingObservation


def test_console_fixture_matches_native_runtime_and_protected_api():
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "apps/web/tests/fixtures/tracing-status.json"
        ).read_text(encoding="utf-8")
    )
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    try:
        with telemetry.span("paper.risk.evaluate"):
            pass
        assert telemetry.flush()
        with TestClient(
            create_app(operator_token="fixture-token", clock=lambda: now, telemetry=telemetry)
        ) as client:
            assert client.get("/api/v1/observability/tracing").status_code == 401
            # Unauthorized request itself produces an HTTP span. Flush that native span.
            assert telemetry.flush()
            running = client.get(
                "/api/v1/observability/tracing", headers={"X-Operator-Token": "fixture-token"}
            ).json()
            TracingObservation.model_validate(running)
            running["runtime_id"] = "0" * 32
            assert running == fixture["running"]
            assert telemetry.flush()
            telemetry.close()
            stopped = client.get(
                "/api/v1/observability/tracing", headers={"X-Operator-Token": "fixture-token"}
            ).json()
            TracingObservation.model_validate(stopped)
            stopped["runtime_id"] = "0" * 32
            assert stopped == fixture["stopped"]
    finally:
        telemetry.close()
    with TestClient(
        create_app(operator_token="fixture-token", clock=lambda: now, telemetry=PaperTelemetry())
    ) as client:
        disabled = client.get(
            "/api/v1/observability/tracing", headers={"X-Operator-Token": "fixture-token"}
        ).json()
        assert disabled == fixture["disabled"]

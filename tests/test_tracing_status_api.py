"""Protected status reads report original volatile queue observations, never authority."""

from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.api import create_app
from ai_quant_os.paper_telemetry import PaperTelemetry
from ai_quant_os.tracing_status_api import TracingObservation

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
PATH = "/api/v1/observability/tracing"
HEADERS = {"X-Operator-Token": "fixture-token"}


@pytest.mark.parametrize("userinfo", ["@", ":@"])
def test_explicit_local_trace_export_rejects_even_empty_url_userinfo(userinfo):
    from ai_quant_os.paper_telemetry import LocalOtlpExporter

    with pytest.raises(ValueError):
        LocalOtlpExporter(f"http://{userinfo}127.0.0.1:4318/v1/traces")


def test_additive_published_observation_schema_matches_native_model():
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/schemas/tracing-observation.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema == TracingObservation.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(
        TracingObservation(runtime_state="DISABLED", observed_at=NOW).model_dump(mode="json")
    )


def test_disabled_native_status_is_protected_uncached_and_not_zero_delivery():
    telemetry = PaperTelemetry()
    with TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, telemetry=telemetry)
    ) as client:
        assert client.get(PATH).status_code == 401
        response = client.get(PATH, headers=HEADERS)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        value = response.json()
        TracingObservation.model_validate(value)
        assert value["runtime_state"] == "DISABLED" and value["observed_spans"] is None
        assert value["delivery_verified"] is False and value["durable"] is False
        assert value["worker_alive"] is None and value["runtime_id"] is None


def test_actual_sdk_status_preserves_original_identity_and_counts_without_export_flush():
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    try:
        with telemetry.span("paper.risk.evaluate"):
            pass
        assert telemetry.flush()
        before = telemetry.diagnostics()
        with TestClient(
            create_app(operator_token="fixture-token", clock=lambda: NOW, telemetry=telemetry)
        ) as client:
            response = client.get(PATH, headers=HEADERS)
            value = response.json()
            assert response.status_code == 200
            TracingObservation.model_validate(value)
            for key in (
                "runtime_id",
                "observed_spans",
                "acknowledged_spans",
                "dropped_spans",
                "unconfirmed_spans",
            ):
                assert value[key] == before[key]
            assert value["exporter_kind"] == "INJECTED" and value["sdk_version"] == "1.45.0"
            assert "fixture-token" not in response.text and "endpoint" not in response.text
    finally:
        telemetry.close()


def test_revocation_during_original_read_withholds_snapshot():
    telemetry = PaperTelemetry()
    with TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, telemetry=telemetry)
    ) as client:
        grant = client.post("/api/v1/operator/sessions", headers=HEADERS).json()
        headers = {"X-Operator-Session": grant["session_token"]}
        capture = telemetry.diagnostics

        def revoked():
            value = capture()
            assert client.delete("/api/v1/operator/session", headers=headers).status_code == 200
            return value

        telemetry.diagnostics = revoked
        response = client.get(PATH, headers=headers)
        assert response.status_code == 401 and "observed_spans" not in response.json()


def test_broken_diagnostic_source_withholds_counters_and_does_not_disable_controls():
    telemetry = PaperTelemetry()

    def failed():
        raise RuntimeError("private-source-secret")

    telemetry.diagnostics = failed
    with TestClient(
        create_app(operator_token="fixture-token", clock=lambda: NOW, telemetry=telemetry)
    ) as client:
        response = client.get(PATH, headers=HEADERS)
        assert response.status_code == 200 and response.json()["runtime_state"] == "UNAVAILABLE"
        assert (
            response.json()["observed_spans"] is None
            and "private-source-secret" not in response.text
        )
        halt = client.post(
            "/api/v1/control/halt",
            headers={**HEADERS, "Idempotency-Key": "fixture-halt"},
            json={"reason": "paper test"},
        )
        assert halt.status_code == 200 and halt.json()["safety_state"] == "HALT"


def test_incoherent_original_counts_withhold_entire_status():
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    try:
        capture = telemetry.diagnostics

        def altered():
            return {**capture(), "observed_spans": 100}

        telemetry.diagnostics = altered
        with TestClient(
            create_app(operator_token="fixture-token", clock=lambda: NOW, telemetry=telemetry)
        ) as client:
            value = client.get(PATH, headers=HEADERS).json()
            assert value["runtime_state"] == "UNAVAILABLE" and value["runtime_id"] is None
            assert value["observed_spans"] is None and value["worker_alive"] is None
    finally:
        telemetry.close()

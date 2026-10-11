"""Native OpenTelemetry observations must not change paper authority or leak input."""

from datetime import datetime, timedelta, timezone
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.api import create_app
from ai_quant_os.paper_telemetry import PaperTelemetry, telemetry_from_environment

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def payload():
    return {
        "intent_id": "secret-intent",
        "strategy_id": "secret-strategy",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "1000",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "private-world",
        "evidence_refs": ["private-evidence"],
        "created_at": NOW.isoformat(),
    }


def run_order(telemetry):
    app = create_app(operator_token="private-key", clock=lambda: NOW, telemetry=telemetry)
    with TestClient(app) as client:
        headers = {
            "X-Operator-Token": "private-key",
            "Idempotency-Key": "private-idempotency",
            "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        }
        quote = {
            "instrument_id": "EURUSD",
            "bid": "1.1000",
            "ask": "1.1001",
            "observed_at": NOW.isoformat(),
            "world_state_version": "private-world",
        }
        assert client.post("/api/v1/paper/quotes", json=quote, headers=headers).status_code == 200
        accepted = client.post("/api/v1/paper/intents", json=payload(), headers=headers)
        assert accepted.status_code == 200 and accepted.json()["status"] == "ACCEPTED"
        replay = client.post("/api/v1/paper/intents", json=payload(), headers=headers)
        assert replay.json() == accepted.json()
        return accepted


def test_native_sdk_order_trace_links_risk_dispatch_and_replay_without_input_secrets():
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    response = run_order(telemetry)
    assert telemetry.flush()
    spans = exporter.get_finished_spans()
    roots = [
        s
        for s in spans
        if s.name == "paper.http" and s.attributes["http.route"] == "/api/v1/paper/intents"
    ]
    assert len(roots) == 2 and roots[0].parent is None
    trace_id = int(response.headers["X-Paper-Trace-Id"], 16)
    assert roots[0].context.trace_id == trace_id and trace_id != int("1" * 32, 16)
    children = [s for s in spans if s.context.trace_id == trace_id and s.parent]
    assert {s.name for s in children} == {
        "paper.market.snapshot",
        "paper.risk.evaluate",
        "paper.order.submit",
    }
    assert all(s.parent.span_id == roots[0].context.span_id for s in children)
    assert (
        next(s for s in children if s.name == "paper.order.submit").attributes["ai_quant.order.id"]
        == response.json()["order_id"]
    )
    assert any(s.name == "paper.order.replay" for s in spans)
    wire = " ".join(s.to_json() for s in spans)
    for private in (
        "private-key",
        "private-idempotency",
        "secret-intent",
        "secret-strategy",
        "private-world",
        "private-evidence",
        "traceparent",
    ):
        assert private not in wire
    telemetry.close()


def test_disabled_default_has_no_header_or_owned_export_thread(monkeypatch):
    monkeypatch.delenv("AI_QUANT_OTEL_ENABLED", raising=False)
    telemetry = telemetry_from_environment()
    response = run_order(telemetry)
    assert "X-Paper-Trace-Id" not in response.headers
    assert telemetry.state() == {"enabled": False, "export_failures": 0}


def test_native_halt_decision_is_observed_without_weakening_safety_or_creating_order():
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        with TestClient(
            create_app(operator_token="private-key", clock=lambda: NOW, telemetry=telemetry)
        ) as client:
            headers = {"X-Operator-Token": "private-key", "Idempotency-Key": "private-idempotency"}
            quote = {
                "instrument_id": "EURUSD",
                "bid": "1.1000",
                "ask": "1.1001",
                "observed_at": NOW.isoformat(),
                "world_state_version": "private-world",
            }
            assert (
                client.post("/api/v1/paper/quotes", json=quote, headers=headers).status_code == 200
            )
            assert (
                client.post(
                    "/api/v1/control/halt", json={"reason": "private-reason"}, headers=headers
                ).status_code
                == 200
            )
            response = client.post("/api/v1/paper/intents", json=payload(), headers=headers)
            assert response.json()["status"] == "HALT" and response.json()["order_id"] is None
            assert client.get("/api/v1/orders").json()["orders"] == []
        assert telemetry.flush()
        risk = next(s for s in exporter.get_finished_spans() if s.name == "paper.risk.evaluate")
        assert risk.attributes["ai_quant.risk.decision"] == "HALT"
        assert "private-reason" not in " ".join(s.to_json() for s in exporter.get_finished_spans())
    finally:
        telemetry.close()


def test_export_failure_does_not_change_accepted_order_or_leak_exception(caplog):
    from opentelemetry.sdk.trace.export import SpanExporter

    class Broken(SpanExporter):
        def export(self, spans):
            raise RuntimeError("private-exporter-secret")

    telemetry = PaperTelemetry(exporter=Broken())
    assert run_order(telemetry).json()["status"] == "ACCEPTED"
    telemetry.flush()
    assert telemetry.state()["export_failures"] > 0
    assert "private-exporter-secret" not in caplog.text
    telemetry.close()


def test_full_native_export_queue_and_blocked_worker_cannot_block_order_or_risk():
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    entered, release = threading.Event(), threading.Event()

    class Slow(SpanExporter):
        def export(self, spans):
            entered.set()
            assert release.wait(10)
            return SpanExportResult.SUCCESS

    telemetry = PaperTelemetry(exporter=Slow())
    try:
        for _ in range(40):
            with telemetry.span("paper.http", root=True):
                pass
        assert entered.wait(3)
        for _ in range(600):
            with telemetry.span("paper.http", root=True):
                pass
        response = run_order(telemetry)
        assert response.json()["status"] == "ACCEPTED" and not release.is_set()
    finally:
        release.set()
        telemetry.close()


def test_lifespan_closes_only_its_owned_runtime(monkeypatch):
    import ai_quant_os.api as api

    owned = PaperTelemetry(exporter=InMemorySpanExporter())
    monkeypatch.setattr(api, "telemetry_from_environment", lambda: owned)
    with TestClient(create_app(operator_token="private-key")) as client:
        assert client.get("/api/v1/system/status").status_code == 200
        assert owned.state()["enabled"]
    assert not owned.state()["enabled"]
    owned.close()


def test_failed_native_span_start_cannot_disable_order_or_risk():
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())

    class BrokenTracer:
        def start_as_current_span(self, *args, **kwargs):
            raise RuntimeError("private-start-error")

    telemetry._tracer = BrokenTracer()
    try:
        response = run_order(telemetry)
        assert (
            response.json()["status"] == "ACCEPTED" and "X-Paper-Trace-Id" not in response.headers
        )
    finally:
        telemetry.close()


def test_native_failure_status_omits_exception_message_and_stack():
    from opentelemetry.trace import StatusCode

    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        with pytest.raises(RuntimeError, match="private-operation-secret"):
            with telemetry.span("paper.risk.evaluate"):
                raise RuntimeError("private-operation-secret")
        assert telemetry.flush()
        span = exporter.get_finished_spans()[0]
        assert (
            span.status.status_code == StatusCode.ERROR
            and span.status.description == "Paper operation failed"
        )
        assert not span.events and "private-operation-secret" not in span.to_json()
    finally:
        telemetry.close()


def test_partial_otlp_acceptance_is_an_observable_failure_without_response_leak(monkeypatch):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
    from ai_quant_os.paper_telemetry import LocalOtlpExporter
    from opentelemetry.sdk.trace.export import SpanExportResult

    acknowledgement = ExportTraceServiceResponse()
    acknowledgement.partial_success.rejected_spans = 1
    acknowledgement.partial_success.error_message = "private-collector-error"
    actual = __import__("httpx")
    transport = actual.MockTransport(
        lambda request: actual.Response(
            200, stream=actual.ByteStream(acknowledgement.SerializeToString())
        )
    )
    original = actual.Client
    monkeypatch.setattr(actual, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    assert (
        LocalOtlpExporter("http://127.0.0.1:4318/v1/traces").export([]) == SpanExportResult.FAILURE
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://remote/v1/traces",
        "http://127.0.0.1:4318/other",
        "http://user:secret@127.0.0.1:4318/v1/traces",
        "http://localhost:4318/v1/traces",
        "http://127.0.0.1:4318/v1/traces?key=secret",
    ],
)
def test_export_endpoint_rejects_credentials_remote_hosts_and_unknown_paths(monkeypatch, endpoint):
    monkeypatch.setenv("AI_QUANT_OTEL_ENABLED", "1")
    monkeypatch.setenv("AI_QUANT_OTEL_ENDPOINT", endpoint)
    with pytest.raises(ValueError):
        telemetry_from_environment()


def test_actual_local_otlp_protobuf_contains_only_explicit_resource_and_trace_fields(monkeypatch):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    dict(self.headers),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        monkeypatch.setenv("AI_QUANT_OTEL_ENABLED", "1")
        monkeypatch.setenv(
            "AI_QUANT_OTEL_ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1/traces"
        )
        monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "private.secret=must-not-export")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=private-header")
        telemetry = telemetry_from_environment()
        run_order(telemetry)
        assert telemetry.flush()
        telemetry.close()
        assert received and all(path == "/v1/traces" for path, _, _ in received)
        assert all("Authorization" not in headers for _, headers, _ in received)
        parsed = ExportTraceServiceRequest.FromString(received[0][2])
        wire = str(parsed)
        assert "service.name" in wire and "paper.risk.evaluate" in wire
        assert "must-not-export" not in wire and "private-key" not in wire
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()

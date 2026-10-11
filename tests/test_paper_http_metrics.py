"""Native SDK HTTP histograms, finite labels and protected original scrapes."""

import asyncio
import re

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.api import create_app
from ai_quant_os.paper_telemetry import PaperTelemetry, PaperTraceMiddleware
from tests.test_paper_telemetry import NOW, run_order

PATH = "/api/v1/observability/metrics"
HEADERS = {"X-Operator-Token": "private-key", "Idempotency-Key": "private-metrics-command"}
NAME = "ai_quant_paper_http_duration_seconds"


def test_native_sdk_histogram_has_exact_cumulative_buckets_without_exemplars():
    from ai_quant_os.paper_http_metrics import PaperHttpMetrics

    metrics = PaperHttpMetrics()
    try:
        metrics.record("/api/v1/orders", "GET", 200, 5_000_000, False)
        metrics.record("/api/v1/orders", "GET", 200, 15_000_000, True)
        labels = 'method="GET",route="/api/v1/orders",status_class="2xx"'
        text = metrics.render()
        assert f'{NAME}_bucket{{{labels},le="0.005"}} 1\n' in text
        assert f'{NAME}_bucket{{{labels},le="0.01"}} 1\n' in text
        assert f'{NAME}_bucket{{{labels},le="0.025"}} 2\n' in text
        assert f'{NAME}_bucket{{{labels},le="+Inf"}} 2\n' in text
        assert f"{NAME}_count{{{labels}}} 2\n" in text
        assert f"{NAME}_sum{{{labels}}} 0.02\n" in text
        assert f"ai_quant_paper_http_failures_total{{{labels}}} 1\n" in text
        assert metrics.render() == text
        assert text.endswith("\n") and "trace_id" not in text and "span_id" not in text
    finally:
        metrics.close()


def test_native_metrics_bound_all_label_combinations_and_retain_overflow_counts():
    from ai_quant_os.paper_http_metrics import PaperHttpMetrics

    metrics = PaperHttpMetrics()
    try:
        for index in range(300):
            metrics.record(f"/api/v1/route-{index}", "GET", 200, 1_000_000, False)
        text = metrics.render()
        rows = [line for line in text.splitlines() if line.startswith(NAME + "_count{")]
        assert len(rows) == 129
        assert sum(int(line.rsplit(" ", 1)[1]) for line in rows) == 300
        assert any('route="OTHER_ROUTE"' in line and line.endswith(" 172") for line in rows)
        assert len(text.encode()) <= 1_048_576
    finally:
        metrics.close()


def test_actual_order_api_is_measured_and_scrape_is_protected_uncached():
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    try:
        assert run_order(telemetry).json()["status"] == "ACCEPTED"
        with TestClient(
            create_app(operator_token="private-key", clock=lambda: NOW, telemetry=telemetry)
        ) as client:
            assert client.get(PATH).status_code == 401
            response = client.get(PATH, headers=HEADERS)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
            text = response.text
            labels = 'method="POST",route="/api/v1/paper/intents",status_class="2xx"'
            assert f"{NAME}_count{{{labels}}} 2\n" in text
            for secret in [
                "private-key",
                "secret-intent",
                "secret-strategy",
                "private-world",
                "private-idempotency",
            ]:
                assert secret not in text
            assert (
                client.post(
                    "/api/v1/control/halt", headers=HEADERS, json={"reason": "private-reason"}
                ).status_code
                == 200
            )
    finally:
        telemetry.close()


def test_disabled_or_broken_metrics_never_infer_zero_success_or_expose_error(monkeypatch):
    telemetry = PaperTelemetry()
    with TestClient(create_app(operator_token="private-key", telemetry=telemetry)) as client:
        response = client.get(PATH, headers=HEADERS)
        assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
        assert NAME not in response.text

        def broken():
            raise RuntimeError("private-metric-error")

        monkeypatch.setattr(telemetry, "metrics_text", broken)
        assert client.get(PATH).status_code == 401
        failed = client.get(PATH, headers=HEADERS)
        assert failed.status_code == 503 and "private-metric-error" not in failed.text
        assert (
            client.post(
                "/api/v1/control/halt", headers=HEADERS, json={"reason": "fixture"}
            ).status_code
            == 200
        )


def test_original_metrics_are_not_read_before_auth_and_revocation_withholds_scrape(monkeypatch):
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    original = telemetry.metrics_text
    reads = []

    def capture():
        reads.append(True)
        return original()

    monkeypatch.setattr(telemetry, "metrics_text", capture)
    try:
        with TestClient(create_app(operator_token="private-key", telemetry=telemetry)) as client:
            assert client.get(PATH).status_code == 401 and reads == []
            assert client.get(PATH, headers=HEADERS).status_code == 200 and len(reads) == 1
            session = client.post("/api/v1/operator/sessions", headers=HEADERS).json()[
                "session_token"
            ]
            headers = {"X-Operator-Session": session}

            def revoke():
                text = original()
                assert client.delete("/api/v1/operator/session", headers=headers).status_code == 200
                return text

            monkeypatch.setattr(telemetry, "metrics_text", revoke)
            response = client.get(PATH, headers=headers)
            assert response.status_code == 401 and NAME not in response.text
    finally:
        telemetry.close()


def test_failed_native_metric_record_cannot_disable_order_or_leak_exception(monkeypatch):
    from ai_quant_os.paper_http_metrics import PaperHttpMetrics

    def broken(*args):
        raise RuntimeError("private-metric-record-error")

    monkeypatch.setattr(PaperHttpMetrics, "record", broken)
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    try:
        response = run_order(telemetry)
        assert response.json()["status"] == "ACCEPTED"
        assert telemetry.diagnostics()["instrumentation_errors"] == 3
        assert "private-metric-record-error" not in response.text
    finally:
        telemetry.close()


def test_parallel_native_records_keep_exact_histogram_inventory_after_reads():
    from concurrent.futures import ThreadPoolExecutor
    from ai_quant_os.paper_http_metrics import PaperHttpMetrics

    metrics = PaperHttpMetrics()
    try:

        def record(index):
            metrics.record("/api/v1/orders", "GET", 200, 5_000_000, index % 2 == 0)
            return metrics.render()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(record, range(100)))
        labels = 'method="GET",route="/api/v1/orders",status_class="2xx"'
        text = metrics.render()
        assert f"{NAME}_count{{{labels}}} 100\n" in text
        assert f'{NAME}_bucket{{{labels},le="+Inf"}} 100\n' in text
        assert f"ai_quant_paper_http_failures_total{{{labels}}} 50\n" in text
    finally:
        metrics.close()
    with pytest.raises(ValueError):
        metrics.render()


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_actual_asgi_failure_is_counted_without_raw_url_or_exception(failure):
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())

    async def broken(scope, receive, send):
        raise failure("private-operation-error")

    async def ignored(*args):
        pass

    try:
        with pytest.raises(failure):
            asyncio.run(
                PaperTraceMiddleware(broken, telemetry=telemetry)(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": "/private-url-token",
                        "query_string": b"password=secret",
                    },
                    ignored,
                    ignored,
                )
            )
        text = telemetry.metrics_text()
        assert 'route="UNMATCHED",status_class="UNKNOWN"' in text
        assert re.search(r"ai_quant_paper_http_failures_total\{[^}]+\} 1\n", text)
        assert "private" not in text and "password" not in text
    finally:
        telemetry.close()


def test_stream_return_without_terminal_body_is_observed_as_incomplete():
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())

    async def incomplete(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})

    async def ignored(*args):
        pass

    try:
        asyncio.run(
            PaperTraceMiddleware(incomplete, telemetry=telemetry)(
                {"type": "http", "method": "GET", "path": "/private-url-token"}, ignored, ignored
            )
        )
        labels = 'method="GET",route="UNMATCHED",status_class="2xx"'
        text = telemetry.metrics_text()
        assert f"{NAME}_count{{{labels}}} 1\n" in text
        assert f"ai_quant_paper_http_failures_total{{{labels}}} 1\n" in text
        assert "private-url-token" not in text
    finally:
        telemetry.close()

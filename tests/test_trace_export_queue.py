"""Native SDK queue accounting is coherent under blocked export and shutdown."""

import threading

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.paper_telemetry import PaperTelemetry


def check_totals(value):
    assert value["observed_spans"] == sum(
        value[key]
        for key in (
            "acknowledged_spans",
            "unconfirmed_spans",
            "dropped_spans",
            "pending_spans",
            "inflight_spans",
        )
    )
    assert value["pending_spans"] <= 512 and value["inflight_spans"] <= 32


def test_native_blocked_queue_counts_exact_saturation_and_retains_one_runtime():
    entered, release = threading.Event(), threading.Event()

    class Blocked(SpanExporter):
        def export(self, spans):
            entered.set()
            assert release.wait(10)
            return SpanExportResult.SUCCESS

    telemetry = PaperTelemetry(exporter=Blocked())
    try:
        for _ in range(32):
            with telemetry.span("paper.http", root=True):
                pass
        assert entered.wait(3)
        for _ in range(600):
            with telemetry.span("paper.http", root=True):
                pass
        value = telemetry.diagnostics()
        check_totals(value)
        assert value["runtime_state"] == "RUNNING" and value["worker_alive"]
        assert value["observed_spans"] == 632 and value["inflight_spans"] == 32
        assert value["pending_spans"] == 512 and value["dropped_spans"] == 88
        identity = value["runtime_id"]
        assert telemetry.flush(timeout_millis=1) is False
        release.set()
        assert telemetry.flush()
        value = telemetry.diagnostics()
        check_totals(value)
        assert value["runtime_id"] == identity and value["acknowledged_spans"] == 544
        assert (
            value["dropped_spans"] == 88 and value["pending_spans"] == value["inflight_spans"] == 0
        )
    finally:
        release.set()
        telemetry.close()
    value = telemetry.diagnostics()
    check_totals(value)
    assert value["runtime_state"] == "STOPPED" and not value["worker_alive"]


def test_native_exporter_failure_preserves_unconfirmed_counts_and_no_secret_logs(caplog):
    class Broken(SpanExporter):
        def export(self, spans):
            raise RuntimeError("private-exporter-secret")

    telemetry = PaperTelemetry(exporter=Broken())
    try:
        with telemetry.span("paper.http", root=True):
            pass
        assert telemetry.flush()
        value = telemetry.diagnostics()
        check_totals(value)
        assert value["unconfirmed_spans"] == 1 and value["failed_batches"] == 1
        assert value["acknowledged_spans"] == value["dropped_spans"] == 0
        assert telemetry.state()["export_failures"] == 1
        assert "private-exporter-secret" not in caplog.text
    finally:
        telemetry.close()


def test_disabled_diagnostics_infer_no_export_counts_or_worker():
    value = PaperTelemetry().diagnostics()
    assert value == {"runtime_state": "DISABLED"}


def test_shutdown_retains_live_original_worker_until_its_export_finishes():
    entered, release = threading.Event(), threading.Event()

    class Blocked(SpanExporter):
        def export(self, spans):
            entered.set()
            assert release.wait(10)
            return SpanExportResult.SUCCESS

    telemetry = PaperTelemetry(exporter=Blocked())
    try:
        for _ in range(32):
            with telemetry.span("paper.http", root=True):
                pass
        assert entered.wait(3)
        for _ in range(7):
            with telemetry.span("paper.http", root=True):
                pass
        before = telemetry.diagnostics()
        telemetry.close()
        held = telemetry.diagnostics()
        check_totals(held)
        assert held["runtime_state"] == "STOPPING" and held["worker_alive"]
        assert (
            held["runtime_id"] == before["runtime_id"]
            and held["dropped_spans"] == 7
            and held["inflight_spans"] == 32
        )
        release.set()
        telemetry.close()
        final = telemetry.diagnostics()
        check_totals(final)
        assert final["runtime_state"] == "STOPPED" and final["acknowledged_spans"] == 32
        assert final["dropped_spans"] == 7 and final["runtime_id"] == before["runtime_id"]
    finally:
        release.set()
        telemetry.close()


def test_unexpected_worker_exit_retains_failed_state_and_future_drops_without_restart(caplog):
    class Exits(SpanExporter):
        def export(self, spans):
            raise SystemExit("private-worker-exit")

    telemetry = PaperTelemetry(exporter=Exits())
    try:
        for _ in range(32):
            with telemetry.span("paper.http", root=True):
                pass
        telemetry._processor._worker.join(timeout=3)
        assert not telemetry._processor._worker.is_alive()
        value = telemetry.diagnostics()
        check_totals(value)
        assert value["runtime_state"] == "FAILED" and value["unconfirmed_spans"] == 32
        assert telemetry.flush() is False
        with telemetry.span("paper.http", root=True):
            pass
        later = telemetry.diagnostics()
        check_totals(later)
        assert later["runtime_id"] == value["runtime_id"] and later["dropped_spans"] == 1
        assert later["runtime_state"] == "FAILED" and "private-worker-exit" not in caplog.text
    finally:
        telemetry.close()


def test_native_span_creation_error_is_visible_without_counting_a_finished_span():
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())

    class BrokenTracer:
        def start_as_current_span(self, *args, **kwargs):
            raise RuntimeError("private-start-secret")

    telemetry._tracer = BrokenTracer()
    try:
        with telemetry.span("paper.http", root=True):
            pass
        value = telemetry.diagnostics()
        check_totals(value)
        assert value["instrumentation_errors"] == 1 and value["observed_spans"] == 0
        assert "private-start-secret" not in str(value)
    finally:
        telemetry.close()

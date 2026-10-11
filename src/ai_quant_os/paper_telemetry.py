"""Owned, optional OTel paper observations; telemetry cannot grant authority."""

from contextlib import contextmanager
import os
import re
from threading import RLock
from time import monotonic
from uuid import uuid4
from importlib.metadata import version

from urllib.parse import urlsplit

import httpx


class PaperSpan:
    def __init__(self, span=None, on_failure=lambda: None):
        self._span = span
        self._on_failure = on_failure

    @property
    def trace_id(self):
        try:
            context = self._span.get_span_context() if self._span else None
            return format(context.trace_id, "032x") if context and context.is_valid else None
        except Exception:
            self._on_failure()
            return None

    def set(self, key, value):
        allowed = (
            key == "http.route"
            and isinstance(value, str)
            and (value == "UNMATCHED" or re.fullmatch(r"/api/v1/[A-Za-z0-9_/{}/.-]{1,240}", value))
            or key == "http.request.method"
            and value in {"GET", "POST", "DELETE", "PUT", "PATCH", "OTHER"}
            or key == "http.response.status_code"
            and type(value) is int
            and 100 <= value <= 599
            or key == "ai_quant.risk.decision"
            and value in {"APPROVE", "REJECT", "HALT"}
            or key == "ai_quant.order.status"
            and value
            in {
                "PENDING",
                "DISPATCHING",
                "ACCEPTED",
                "REJECTED",
                "UNKNOWN",
                "FILLED",
                "PARTIALLY_FILLED",
            }
            or key == "ai_quant.agent.route"
            and value in {"DEEP_REVIEW", "WAIT_FOR_DATA"}
            or key == "ai_quant.review.status"
            and value
            in {
                "COMPLETED",
                "NOT_CONFIGURED",
                "WAIT_FOR_DATA",
                "BUSY",
                "TIMEOUT",
                "FAILED",
                "INVALID",
            }
            or key
            in {
                "ai_quant.order.id",
                "ai_quant.agent.envelope_id",
                "ai_quant.agent.parent_envelope_id",
            }
            and isinstance(value, str)
            and re.fullmatch(r"[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}", value)
        )
        if allowed and self._span:
            try:
                self._span.set_attribute(key, value)
            except Exception:
                self._on_failure()


class PaperTelemetry:
    def __init__(self, *, exporter=None):
        self._provider = None
        self._tracer = None
        self._processor = None
        self._runtime_id = None
        self._sdk_version = None
        self._exporter_kind = None
        self._instrumentation_errors = 0
        self._closing = False
        self._lock = RLock()
        if exporter is None:
            return
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import SpanLimits, TracerProvider
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
        from ai_quant_os.trace_export_queue import TraceExportQueue

        self._runtime_id = uuid4().hex
        self._sdk_version = version("opentelemetry-sdk")
        self._exporter_kind = (
            "LOCAL_OTLP_PROTOBUF" if type(exporter) is LocalOtlpExporter else "INJECTED"
        )
        self._provider = TracerProvider(
            resource=Resource(
                {
                    "service.name": "ai-quant-paper",
                    "service.version": "0.1.0",
                    "ai_quant.mode": "PAPER",
                }
            ),
            sampler=ALWAYS_ON,
            shutdown_on_exit=False,
            span_limits=SpanLimits(
                max_attributes=8, max_events=0, max_links=0, max_attribute_length=256
            ),
        )
        self._processor = TraceExportQueue(exporter, lock=self._lock)
        self._provider.add_span_processor(self._processor)
        self._tracer = self._provider.get_tracer("ai_quant_os.paper", "1.0")

    @contextmanager
    def span(self, name, *, root=False):
        names = {
            "paper.http",
            "paper.market.snapshot",
            "paper.risk.evaluate",
            "paper.order.submit",
            "paper.order.replay",
            "paper.order.recover",
            "paper.agent.classify",
            "paper.agent.review",
            "paper.agent.archive",
            "paper.provider.classify",
            "paper.provider.review",
        }
        manager = None
        native = None
        if self._tracer and not self._closing and name in names:
            try:
                from opentelemetry.context import Context
                from opentelemetry.trace import SpanKind

                candidate = self._tracer.start_as_current_span(
                    name,
                    context=Context() if root else None,
                    kind=SpanKind.SERVER if root else SpanKind.INTERNAL,
                    record_exception=False,
                    set_status_on_exception=False,
                    attributes={"ai_quant.mode": "PAPER"},
                )
                native = candidate.__enter__()
                manager = candidate
            except Exception:
                with self._lock:
                    self._instrumentation_errors += 1
        try:
            yield PaperSpan(native, self._note_error)
        except BaseException:
            if native:
                try:
                    from opentelemetry.trace import Status, StatusCode

                    native.set_status(Status(StatusCode.ERROR, "Paper operation failed"))
                except Exception:
                    self._note_error()
            raise
        finally:
            if manager:
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    self._note_error()

    def _note_error(self):
        with self._lock:
            self._instrumentation_errors += 1

    def state(self):
        with self._lock:
            return {
                "enabled": self._provider is not None and not self._closing,
                "export_failures": self._processor.snapshot()["failed_batches"]
                if self._processor
                else 0,
            }

    def diagnostics(self):
        with self._lock:
            if self._processor is None:
                return {"runtime_state": "DISABLED"}
            return {
                **self._processor.snapshot(),
                "runtime_id": self._runtime_id,
                "sdk_version": self._sdk_version,
                "exporter_kind": self._exporter_kind,
                "instrumentation_errors": self._instrumentation_errors,
            }

    def flush(self, timeout_millis=3000):
        return self._processor.force_flush(timeout_millis) if self._processor else True

    def close(self):
        with self._lock:
            was_closing = self._closing
            self._closing = True
        if self._provider:
            if was_closing:
                self._processor.shutdown()
            else:
                try:
                    self._provider.shutdown()
                except Exception:
                    self._note_error()
                    self._processor.shutdown()


class LocalOtlpExporter:
    def __init__(self, endpoint):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/v1/traces"
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or parsed.port == 0
        ):
            raise ValueError("Explicit local OTel collector required")
        self._endpoint = endpoint

    def export(self, spans):
        from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
        from opentelemetry.sdk.trace.export import SpanExportResult
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceResponse,
        )

        body = encode_spans(spans).SerializeToString()
        if len(body) > 262144:
            return SpanExportResult.FAILURE
        # Only the batch thread performs I/O; no retries, redirects, ambient proxies or headers.
        deadline = monotonic() + 2
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
            with client.stream(
                "POST",
                self._endpoint,
                content=body,
                headers={"Content-Type": "application/x-protobuf"},
            ) as response:
                if response.status_code != 200:
                    return SpanExportResult.FAILURE
                acknowledgement = bytearray()
                for chunk in response.iter_raw():
                    acknowledgement.extend(chunk)
                    if len(acknowledgement) > 4096 or monotonic() > deadline:
                        return SpanExportResult.FAILURE
                decoded = ExportTraceServiceResponse.FromString(bytes(acknowledgement))
                if decoded.partial_success.rejected_spans or decoded.partial_success.error_message:
                    return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def telemetry_from_environment():
    enabled = os.getenv("AI_QUANT_OTEL_ENABLED", "0")
    if enabled == "0":
        return PaperTelemetry()
    if enabled != "1":
        raise ValueError("Explicit OTel enable flag required")
    return PaperTelemetry(
        exporter=LocalOtlpExporter(
            os.getenv("AI_QUANT_OTEL_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
        )
    )


class PaperTraceMiddleware:
    def __init__(self, app, *, telemetry):
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        with self.telemetry.span("paper.http", root=True) as span:
            method = scope.get("method")
            span.set(
                "http.request.method",
                method if method in {"GET", "POST", "DELETE", "PUT", "PATCH"} else "OTHER",
            )

            async def observe(message):
                if message["type"] == "http.response.start":
                    route = scope.get("route")
                    span.set("http.route", getattr(route, "path", "UNMATCHED"))
                    span.set("http.response.status_code", message["status"])
                    if span.trace_id:
                        message = {
                            **message,
                            "headers": [
                                *message.get("headers", []),
                                (b"x-paper-trace-id", span.trace_id.encode("ascii")),
                            ],
                        }
                await send(message)

            await self.app(scope, receive, observe)

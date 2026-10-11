"""Owned, optional OTel paper observations; telemetry cannot grant authority."""

from contextlib import contextmanager
import os
import re
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

import httpx


class PaperSpan:
    def __init__(self, span=None):
        self._span = span

    @property
    def trace_id(self):
        try:
            context = self._span.get_span_context() if self._span else None
            return format(context.trace_id, "032x") if context and context.is_valid else None
        except Exception:
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
            or key == "ai_quant.order.id"
            and isinstance(value, str)
            and re.fullmatch(r"[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}", value)
        )
        if allowed and self._span:
            try:
                self._span.set_attribute(key, value)
            except Exception:
                pass


class PaperTelemetry:
    def __init__(self, *, exporter=None):
        self._provider = None
        self._tracer = None
        self._failures = 0
        self._closing = False
        self._lock = Lock()
        if exporter is None:
            return
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import SpanLimits, TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            SpanExporter,
            SpanExportResult,
        )
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON

        owner = self

        class SafeExporter(SpanExporter):
            def export(self, spans):
                with owner._lock:
                    if owner._closing:
                        return SpanExportResult.FAILURE
                try:
                    result = exporter.export(spans)
                    if result == SpanExportResult.SUCCESS:
                        return result
                except Exception:
                    pass
                with owner._lock:
                    owner._failures += 1
                return SpanExportResult.FAILURE

            def shutdown(self):
                try:
                    exporter.shutdown()
                except Exception:
                    pass

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
        self._provider.add_span_processor(
            BatchSpanProcessor(
                SafeExporter(),
                max_queue_size=512,
                max_export_batch_size=32,
                schedule_delay_millis=1000,
                export_timeout_millis=3000,
            )
        )
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
                pass
        try:
            yield PaperSpan(native)
        except BaseException:
            if native:
                try:
                    from opentelemetry.trace import Status, StatusCode

                    native.set_status(Status(StatusCode.ERROR, "Paper operation failed"))
                except Exception:
                    pass
            raise
        finally:
            if manager:
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    pass

    def state(self):
        with self._lock:
            return {
                "enabled": self._provider is not None and not self._closing,
                "export_failures": self._failures,
            }

    def flush(self):
        try:
            return self._provider.force_flush(timeout_millis=3000) if self._provider else True
        except Exception:
            return False

    def close(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
        if self._provider:
            try:
                self._provider.shutdown()
            except Exception:
                pass


class LocalOtlpExporter:
    def __init__(self, endpoint):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
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

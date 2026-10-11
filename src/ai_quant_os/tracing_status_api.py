"""Protected volatile trace accounting; no delivery or trading authority claims."""

from datetime import datetime, timezone
from typing import Annotated, Callable, Literal

from fastapi import Depends, FastAPI, Header, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.contracts import _require_aware
from ai_quant_os.paper_telemetry import PaperTelemetry

Count = Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]


class TracingObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    accounting: Literal["VOLATILE_FINISHED_SDK_SPANS"] = "VOLATILE_FINISHED_SDK_SPANS"
    durable: Literal[False] = False
    delivery_verified: Literal[False] = False
    runtime_state: Literal["DISABLED", "RUNNING", "STOPPING", "STOPPED", "FAILED", "UNAVAILABLE"]
    observed_at: AwareDatetime
    runtime_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    sdk_version: str | None = Field(default=None, pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
    exporter_kind: Literal["LOCAL_OTLP_PROTOBUF", "INJECTED"] | None = None
    worker_alive: bool | None = Field(default=None, strict=True)
    queue_capacity: Literal[512] | None = None
    batch_capacity: Literal[32] | None = None
    observed_spans: Count | None = None
    acknowledged_spans: Count | None = None
    unconfirmed_spans: Count | None = None
    dropped_spans: Count | None = None
    pending_spans: Count | None = Field(default=None, le=512)
    inflight_spans: Count | None = Field(default=None, le=32)
    acknowledged_batches: Count | None = None
    failed_batches: Count | None = None
    instrumentation_errors: Count | None = None

    @model_validator(mode="after")
    def coherent(self):
        values = (
            self.runtime_id,
            self.sdk_version,
            self.exporter_kind,
            self.worker_alive,
            self.queue_capacity,
            self.batch_capacity,
            self.observed_spans,
            self.acknowledged_spans,
            self.unconfirmed_spans,
            self.dropped_spans,
            self.pending_spans,
            self.inflight_spans,
            self.acknowledged_batches,
            self.failed_batches,
            self.instrumentation_errors,
        )
        if self.observed_at.utcoffset().total_seconds() != 0:
            raise ValueError("UTC status observation required")
        if self.runtime_state in ("DISABLED", "UNAVAILABLE"):
            if any(value is not None for value in values):
                raise ValueError("Unavailable tracing cannot infer counters")
            return self
        if any(value is None for value in values):
            raise ValueError("Complete original runtime observation required")
        if self.observed_spans != (
            self.acknowledged_spans
            + self.unconfirmed_spans
            + self.dropped_spans
            + self.pending_spans
            + self.inflight_spans
        ):
            raise ValueError("Trace counter inventory differs")
        for spans, batches in (
            (self.acknowledged_spans, self.acknowledged_batches),
            (self.unconfirmed_spans, self.failed_batches),
        ):
            if not batches <= spans <= 32 * batches:
                raise ValueError("Trace batch counts differ from outcomes")
        if self.runtime_state == "RUNNING" and not self.worker_alive:
            raise ValueError("Running trace worker must be observed alive")
        if self.runtime_state == "STOPPED" and (
            self.worker_alive or self.pending_spans or self.inflight_spans
        ):
            raise ValueError("Terminal worker cannot infer pending custody")
        if self.runtime_state == "FAILED" and (self.pending_spans or self.inflight_spans):
            raise ValueError("Failed worker cannot retain queued export work")
        if self.runtime_state == "STOPPING" and (not self.worker_alive or self.pending_spans):
            raise ValueError("Stopping retains only an original observed live worker")
        return self


def observe_tracing(telemetry: PaperTelemetry, now: datetime) -> TracingObservation:
    _require_aware(now)
    now = now.astimezone(timezone.utc)
    try:
        raw = telemetry.diagnostics()
        if raw == {"runtime_state": "DISABLED"}:
            return TracingObservation(runtime_state="DISABLED", observed_at=now)
        return TracingObservation.model_validate(
            {**raw, "observed_at": now, "queue_capacity": 512, "batch_capacity": 32}
        )
    except Exception:
        return TracingObservation(runtime_state="UNAVAILABLE", observed_at=now)


def install_tracing_status_routes(
    app: FastAPI,
    telemetry: PaperTelemetry,
    require_operator: Callable,
    clock: Callable[[], datetime],
) -> None:
    @app.get(
        "/api/v1/observability/tracing",
        response_model=TracingObservation,
        dependencies=[Depends(require_operator)],
    )
    def read(
        response: Response,
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
    ):
        observation = observe_tracing(telemetry, clock())
        require_operator(x_operator_token, x_operator_session)
        response.headers["Cache-Control"] = "no-store"
        return observation

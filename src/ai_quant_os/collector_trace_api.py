"""Protected selected local collector observations with no delivery authority."""

from datetime import timezone
from typing import Annotated, Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.collector_trace import CollectorSpan, TRACE_PATTERN
from ai_quant_os.collector_trace_source import CollectorTraceSource, MAX_FILE
from ai_quant_os.contracts import _require_aware


class CollectorFilePrefix(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ordinal: int = Field(strict=True, ge=0, le=3)
    prefix_bytes: int = Field(strict=True, ge=0, le=MAX_FILE)
    prefix_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CollectorTraceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    delivery_verified: Literal[False] = False
    independently_attested: Literal[False] = False
    complete_trace: Literal[False] = False
    trace_id: str = Field(pattern=TRACE_PATTERN)
    observed_at: AwareDatetime
    state: Literal["RECORDED_SPANS", "NOT_OBSERVED", "DISABLED", "UNAVAILABLE"]
    spans: tuple[CollectorSpan, ...] = Field(default=(), max_length=128)
    files: tuple[CollectorFilePrefix, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def coherent(self):
        if self.observed_at.utcoffset().total_seconds() != 0 or int(self.trace_id, 16) == 0:
            raise ValueError("UTC observation and nonzero requested trace required")
        if self.state in {"DISABLED", "UNAVAILABLE"}:
            if self.spans or self.files:
                raise ValueError("Unavailable source cannot infer records")
        elif (
            not self.files
            or tuple(file.ordinal for file in self.files) != tuple(range(len(self.files)))
            or any(span.trace_id != self.trace_id for span in self.spans)
            or len({span.span_id for span in self.spans}) != len(self.spans)
            or bool(self.spans) != (self.state == "RECORDED_SPANS")
        ):
            raise ValueError("Coherent original prefixes and selected trace required")
        return self


def install_collector_trace_routes(
    app: FastAPI, source: CollectorTraceSource, require_operator: Callable, clock: Callable
):
    @app.get(
        "/api/v1/observability/traces/{trace_id}",
        response_model=CollectorTraceObservation,
        dependencies=[Depends(require_operator)],
    )
    def read(
        trace_id: Annotated[str, Path(pattern=TRACE_PATTERN)],
        response: Response,
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
    ):
        if int(trace_id, 16) == 0:
            raise HTTPException(status_code=422, detail="Nonzero SDK trace ID required")
        try:
            if source.path is None:
                state, spans, files = "DISABLED", (), ()
            else:
                spans, files = source.read(trace_id)
                state = "RECORDED_SPANS" if spans else "NOT_OBSERVED"
        except Exception:
            state, spans, files = "UNAVAILABLE", (), ()
        now = clock()
        _require_aware(now)
        now = now.astimezone(timezone.utc)
        try:
            result = CollectorTraceObservation(
                trace_id=trace_id,
                observed_at=now,
                state=state,
                spans=spans,
                files=files,
            )
        except Exception:
            result = CollectorTraceObservation(
                trace_id=trace_id, observed_at=now, state="UNAVAILABLE"
            )
        require_operator(x_operator_token, x_operator_session)
        response.headers["Cache-Control"] = "no-store"
        return result

"""Bound archival transitions; clock observations never establish capital authority."""

import json
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.graph_ledger import RunStatus


def trace_identity(run_id: str, context_ref: str, manifest_ref: str) -> str:
    raw = json.dumps([run_id, context_ref, manifest_ref], separators=(",", ":"))
    return "graph-trace:" + sha256(raw.encode()).hexdigest()


def observation(value: str | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Invalid graph transition observation")
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() is None or moment.utcoffset().total_seconds() != 0:
        raise ValueError("Graph transition observation must use UTC")
    if moment.isoformat() != value:
        raise ValueError("Graph transition observation must be canonical")
    return moment


class GraphTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: int = Field(strict=True, ge=1)
    status: RunStatus
    record_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: AwareDatetime | None

    @model_validator(mode="after")
    def utc(self):
        if self.observed_at is not None and self.observed_at.utcoffset().total_seconds() != 0:
            raise ValueError("Graph trace event requires UTC")
        return self


def timing(events: tuple[GraphTraceEvent, ...]):
    moments = [row.observed_at for row in events]
    if not any(moment is not None for moment in moments):
        return "UNAVAILABLE", None
    if any(moment is None for moment in moments):
        return "PARTIAL", None
    if any(a > b for a, b in zip(moments, moments[1:])):
        return "CLOCK_REGRESSION", None
    running = next((row for row in events if row.status == "RUNNING"), None)
    elapsed = None
    if running is not None and events[-1].status != "RUNNING":
        delta = events[-1].observed_at - running.observed_at
        micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        elapsed = Decimal(micros) / Decimal(1000000)
    return "AVAILABLE", elapsed


class GraphTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    scope: Literal["PAPER", "SANDBOX"]
    archival_only: Literal[True] = True
    requires_escalation: Literal[True] = True
    trace_id: str = Field(pattern=r"^graph-trace:[a-f0-9]{64}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    task_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(pattern=r"^A(?:0[1-9]|1[0-8])$")
    agent_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")
    manifest_ref: str = Field(pattern=r"^agent-manifest:[a-f0-9]{64}$")
    input_refs: tuple[str, ...] = Field(max_length=128)
    context_observed_at: AwareDatetime
    context_valid_until: AwareDatetime
    status: RunStatus
    output_trace_id: str | None = Field(default=None, max_length=128)
    output_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    events: tuple[GraphTraceEvent, ...] = Field(min_length=1, max_length=3)
    timing_state: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "CLOCK_REGRESSION"]
    elapsed_seconds: Decimal | None = Field(ge=0)

    @model_validator(mode="after")
    def bound_timeline(self):
        if any(
            moment.utcoffset().total_seconds() != 0
            for moment in (self.context_observed_at, self.context_valid_until)
        ):
            raise ValueError("Graph trace context observation requires UTC")
        if self.context_valid_until <= self.context_observed_at:
            raise ValueError("Graph trace context expiry mismatch")
        if self.trace_id != trace_identity(self.run_id, self.context_ref, self.manifest_ref):
            raise ValueError("Graph trace identity mismatch")
        states = [row.status for row in self.events]
        if states[0] != "CREATED" or states[-1] != self.status:
            raise ValueError("Graph trace terminal binding mismatch")
        if len(states) == 2 and states[1] in {"CREATED", "COMPLETED", "UNCERTAIN"}:
            raise ValueError("Graph trace transition mismatch")
        if len(states) == 3 and (states[1] != "RUNNING" or states[2] in {"CREATED", "RUNNING"}):
            raise ValueError("Graph trace transition mismatch")
        sequence = [row.sequence for row in self.events]
        if sequence != sorted(set(sequence)):
            raise ValueError("Graph trace event order mismatch")
        if (self.timing_state, self.elapsed_seconds) != timing(self.events):
            raise ValueError("Graph trace timing claim mismatch")
        if (self.status == "COMPLETED") != (self.output_sha256 is not None):
            raise ValueError("Graph trace output/status mismatch")
        if self.status != "COMPLETED" and self.output_trace_id is not None:
            raise ValueError("Graph trace cannot claim an absent output")
        return self

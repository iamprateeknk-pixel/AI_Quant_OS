"""Observed SDK archive context, distinct from delivery or trading authority."""

from datetime import timezone
from hashlib import sha256
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_runs import AgentRun


class AgentTraceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    kind: Literal["OBSERVED_ARCHIVE_SDK_CONTEXT"] = "OBSERVED_ARCHIVE_SDK_CONTEXT"
    delivery_verified: Literal[False] = False
    run_id: str = Field(pattern=r"^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    run_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sdk_version: str = Field(pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
    sdk_trace_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sdk_span_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def observed_context(self):
        if any(
            int(value, 16) == 0 for value in (self.runtime_id, self.sdk_trace_id, self.sdk_span_id)
        ):
            raise ValueError("Original nonzero context required")
        if self.recorded_at.utcoffset().total_seconds() != 0:
            raise ValueError("UTC binding time required")
        return self

    def require_run(self, run: AgentRun):
        if (
            self.run_id != run.run_id
            or self.recorded_at != run.recorded_at
            or self.run_sha256 != sha256(run.model_dump_json().encode()).hexdigest()
        ):
            raise ValueError("Original agent run binding mismatch")

    def audit_event(self):
        return {
            "action": "PAPER_AGENT_TRACE_BINDING",
            "intent_id": "",
            "reason": self.kind,
            "run_id": self.run_id,
            "run_sha256": self.run_sha256,
            "binding_sha256": sha256(self.model_dump_json().encode()).hexdigest(),
        }


def make_agent_trace_binding(run: AgentRun, context: dict | None) -> AgentTraceBinding | None:
    if context is None:
        return None
    try:
        return AgentTraceBinding(
            run_id=run.run_id,
            run_sha256=sha256(run.model_dump_json().encode()).hexdigest(),
            recorded_at=run.recorded_at.astimezone(timezone.utc),
            **context,
        )
    except (ValueError, TypeError):
        # A missing/invalid telemetry observation cannot create a fabricated link.
        return None

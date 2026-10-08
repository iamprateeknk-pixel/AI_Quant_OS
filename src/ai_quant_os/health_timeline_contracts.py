"""Public metadata-only contracts for verified paper incident pages."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.health_contracts import AGENT_CODES, FAILURE_STATES


class HealthIncidentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"]
    incident_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    code: Literal[
        "AUDIT_INVALID",
        "GRAPH_INTEGRITY_FAILED",
        "POSITION_MISMATCH",
        "WATCHDOG_FAILURE",
        "CLOCK_REGRESSION",
        "BROKER_UNCONFIRMED",
        "QUOTE_STALE",
        "QUOTE_FUTURE",
        "QUOTE_UNTRUSTED",
        "AGENT_FAILED",
        "AGENT_TIMEOUT",
        "AGENT_INVALID",
        "AGENT_UNCERTAIN",
    ]
    subject: str = Field(min_length=1, max_length=128)
    detected_at: datetime
    agent_id: str | None = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    agent_version: str | None = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    manifest_ref: str | None = Field(pattern=r"^agent-manifest:[a-f0-9]{64}$")
    safety_state: Literal["HALT", "NO_NEW_RISK"]
    quarantine: bool
    status: Literal["OPEN"]
    promotion_allowed: Literal[False]

    @model_validator(mode="after")
    def original_policy(self) -> "HealthIncidentSummary":
        if (
            self.detected_at.utcoffset() is None
            or self.detected_at.utcoffset().total_seconds() != 0
        ):
            raise ValueError("Incident timestamp must be UTC")
        if self.safety_state != FAILURE_STATES[self.code] or self.quarantine != (
            self.code in AGENT_CODES
        ):
            raise ValueError("Incident policy mismatch")
        pins = (self.agent_id, self.agent_version, self.manifest_ref)
        if (self.quarantine and any(pin is None for pin in pins)) or (
            not self.quarantine and any(pin is not None for pin in pins)
        ):
            raise ValueError("Incident manifest pins mismatch")
        return self


class HealthTimelinePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"]
    mode: Literal["PAPER"]
    head_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    total: int = Field(ge=0, le=1000, strict=True)
    offset: int = Field(ge=0, le=1000, strict=True)
    limit: int = Field(ge=1, le=100, strict=True)
    next_offset: int | None = Field(ge=1, le=1000, strict=True)
    incidents: tuple[HealthIncidentSummary, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def page_bounds(self) -> "HealthTimelinePage":
        expected = self.offset + self.limit if self.offset + self.limit < self.total else None
        if self.next_offset != expected or len(self.incidents) != min(
            self.limit, max(0, self.total - self.offset)
        ):
            raise ValueError("Incident page bounds mismatch")
        if len({incident.incident_id for incident in self.incidents}) != len(self.incidents):
            raise ValueError("Duplicate incident identity")
        return self

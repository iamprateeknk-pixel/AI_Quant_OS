"""Bounded A16 health observations with explicit missing repair evidence."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.health_contracts import HealthIncident

INPUT_NAMES = ("health", "traces", "code_tests", "incident_context")


class HealthObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    status: Literal["UNAVAILABLE", "RESTORED_ONLY", "MONITORED", "ERROR"]
    open_incidents: StrictInt | None = Field(default=None, ge=0, le=10000)

    def model_post_init(self, __context) -> None:
        if (self.status == "UNAVAILABLE") != (self.open_incidents is None):
            raise ValueError("Health observation availability mismatch")


class MissingRepairEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    input_name: Literal["traces", "code_tests"]
    availability: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal["TRACE_ARCHIVE_NOT_CONNECTED", "CODE_TEST_RECEIPTS_NOT_CONNECTED"]


class IncidentContextObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    availability: Literal["UNAVAILABLE", "SUMMARY_ONLY"]
    open_incidents: StrictInt | None = Field(default=None, ge=0, le=10000)

    def model_post_init(self, __context) -> None:
        if (self.availability == "UNAVAILABLE") != (self.open_incidents is None):
            raise ValueError("Incident context availability mismatch")


class IncidentDetail(BaseModel):
    """Minimal incident identity for diagnosis; raw subjects and evidence stay private."""

    model_config = ConfigDict(extra="forbid", frozen=True)
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
    detected_at: AwareDatetime
    agent_id: str | None = Field(default=None, max_length=128)
    agent_version: str | None = Field(default=None, max_length=128)
    manifest_ref: str | None = Field(default=None, max_length=128)


class DetailedIncidentContextObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    availability: Literal["UNAVAILABLE", "DETAILS", "TRUNCATED"]
    total_incidents: StrictInt | None = Field(default=None, ge=0, le=10000)
    incidents: tuple[IncidentDetail, ...] = Field(max_length=8)

    def model_post_init(self, __context) -> None:
        if (
            (
                self.availability == "UNAVAILABLE"
                and (self.total_incidents is not None or self.incidents)
            )
            or (self.availability != "UNAVAILABLE" and self.total_incidents is None)
            or (self.total_incidents is not None and self.total_incidents < len(self.incidents))
            or (self.availability == "DETAILS" and self.total_incidents != len(self.incidents))
            or (self.availability == "TRUNCATED" and self.total_incidents <= len(self.incidents))
        ):
            raise ValueError("Incident detail coverage mismatch")


@dataclass(frozen=True)
class RepairInputs:
    health_status: str
    open_incidents: int | None
    incidents: tuple[HealthIncident, ...] | None = None


def repair_sources(
    inputs: RepairInputs, *, now: datetime, include_incidents: bool = False
) -> dict[str, SourceBinding]:
    if type(now) is not datetime or now.tzinfo is None or type(inputs) is not RepairInputs:
        raise ValueError("Current typed repair observations required")
    if type(include_incidents) is not bool:
        raise ValueError("Explicit incident detail contract required")
    if include_incidents:
        if inputs.incidents is None:
            incident_payload = DetailedIncidentContextObservation(
                as_of=now, availability="UNAVAILABLE", incidents=()
            )
        else:
            if (
                any(type(item) is not HealthIncident for item in inputs.incidents)
                or len(inputs.incidents) != inputs.open_incidents
            ):
                raise ValueError("Verified health incidents required for detailed context")
            recent = sorted(
                inputs.incidents,
                key=lambda item: (item.detected_at, item.incident_id),
                reverse=True,
            )
            details = tuple(
                IncidentDetail(
                    incident_id=item.incident_id,
                    code=item.code,
                    detected_at=item.detected_at,
                    agent_id=item.agent_id,
                    agent_version=item.agent_version,
                    manifest_ref=item.manifest_ref,
                )
                for item in recent[:8]
            )
            incident_payload = DetailedIncidentContextObservation(
                as_of=now,
                availability="TRUNCATED" if len(recent) > len(details) else "DETAILS",
                total_incidents=len(recent),
                incidents=details,
            )
    else:
        incident_payload = IncidentContextObservation(
            as_of=now,
            availability="UNAVAILABLE" if inputs.open_incidents is None else "SUMMARY_ONLY",
            open_incidents=inputs.open_incidents,
        )
    payloads = {
        "health": (
            HealthObservation(
                as_of=now,
                status=inputs.health_status,
                open_incidents=inputs.open_incidents,
            ),
            "HEALTH_READ",
        ),
        "traces": (
            MissingRepairEvidence(
                as_of=now, input_name="traces", reason="TRACE_ARCHIVE_NOT_CONNECTED"
            ),
            "HEALTH_READ",
        ),
        "code_tests": (
            MissingRepairEvidence(
                as_of=now,
                input_name="code_tests",
                reason="CODE_TEST_RECEIPTS_NOT_CONNECTED",
            ),
            "HEALTH_READ",
        ),
        "incident_context": (incident_payload, "HEALTH_READ"),
    }
    sources = {}
    for name, (payload, tool) in payloads.items():
        encoded = json.dumps(
            payload.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        record = EvidenceRecord(
            source_id=f"health-repair:{name}:{sha256(encoded.encode()).hexdigest()}",
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=encoded,
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources

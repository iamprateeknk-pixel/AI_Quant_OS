"""Bounded A16 health observations with explicit missing repair evidence."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding

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


@dataclass(frozen=True)
class RepairInputs:
    health_status: str
    open_incidents: int | None


def repair_sources(inputs: RepairInputs, *, now: datetime) -> dict[str, SourceBinding]:
    if type(now) is not datetime or now.tzinfo is None or type(inputs) is not RepairInputs:
        raise ValueError("Current typed repair observations required")
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
        "incident_context": (
            IncidentContextObservation(
                as_of=now,
                availability="UNAVAILABLE" if inputs.open_incidents is None else "SUMMARY_ONLY",
                open_incidents=inputs.open_incidents,
            ),
            "HEALTH_READ",
        ),
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

"""Bounded A16 health observations with explicit missing repair evidence."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re
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


class RepairTrace(BaseModel):
    """Graph-ledger trace pins linked to the exact immutable health incident."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    incident_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    trace_id: str = Field(pattern=r"^graph-trace:[a-f0-9]{64}$")
    scope: Literal["PAPER", "SANDBOX"]
    context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")
    agent_id: str = Field(pattern=r"^A(?:0[1-9]|1[0-8])$")
    agent_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    manifest_ref: str = Field(pattern=r"^agent-manifest:[a-f0-9]{64}$")
    status: Literal["FAILED", "TIMEOUT", "INVALID", "UNCERTAIN"]
    event_hashes: tuple[str, ...] = Field(min_length=1, max_length=3)
    output_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    def model_post_init(self, __context) -> None:
        if (
            any(re.fullmatch(r"[a-f0-9]{64}", value) is None for value in self.event_hashes)
            or self.output_sha256 is not None
        ):
            raise ValueError("Failed graph trace must contain only valid transition hashes")


class TraceEvidenceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    availability: Literal["UNAVAILABLE", "PARTIAL", "AVAILABLE"]
    expected_count: StrictInt | None = Field(default=None, ge=0, le=8)
    traces: tuple[RepairTrace, ...] = Field(max_length=8)

    def model_post_init(self, __context) -> None:
        coherent = (
            self.expected_count is None and not self.traces
            if self.availability == "UNAVAILABLE"
            else self.expected_count == len(self.traces)
            if self.availability == "AVAILABLE"
            else self.expected_count is not None and self.expected_count > len(self.traces)
        )
        if not coherent:
            raise ValueError("Graph trace coverage mismatch")


@dataclass(frozen=True)
class RepairInputs:
    health_status: str
    open_incidents: int | None
    incidents: tuple[HealthIncident, ...] | None = None
    traces: tuple[RepairTrace, ...] | None = None


def repair_sources(
    inputs: RepairInputs,
    *,
    now: datetime,
    include_incidents: bool = False,
    include_traces: bool = False,
) -> dict[str, SourceBinding]:
    if type(now) is not datetime or now.tzinfo is None or type(inputs) is not RepairInputs:
        raise ValueError("Current typed repair observations required")
    if type(include_incidents) is not bool or type(include_traces) is not bool:
        raise ValueError("Explicit incident and trace detail contract required")
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
    if include_traces:
        if not include_incidents:
            raise ValueError("Trace evidence requires incident identity context")
        if inputs.incidents is None or inputs.traces is None:
            trace_payload = TraceEvidenceObservation(
                as_of=now, availability="UNAVAILABLE", traces=()
            )
        else:
            recent_incidents = sorted(
                inputs.incidents,
                key=lambda item: (item.detected_at, item.incident_id),
                reverse=True,
            )[:8]
            agent_incidents = {
                item.incident_id: item
                for item in recent_incidents
                if item.code.startswith("AGENT_")
            }
            by_incident = {}
            seen_trace_ids = set()
            statuses = {
                "AGENT_FAILED": "FAILED",
                "AGENT_TIMEOUT": "TIMEOUT",
                "AGENT_INVALID": "INVALID",
                "AGENT_UNCERTAIN": "UNCERTAIN",
            }
            for trace in inputs.traces:
                if type(trace) is not RepairTrace:
                    raise ValueError("Verified graph trace summaries required")
                incident = agent_incidents.get(trace.incident_id)
                if (
                    incident is None
                    or trace.incident_id in by_incident
                    or trace.trace_id in seen_trace_ids
                    or (trace.agent_id, trace.agent_version, trace.manifest_ref)
                    != (incident.agent_id, incident.agent_version, incident.manifest_ref)
                    or trace.status != statuses[incident.code]
                ):
                    raise ValueError("Graph trace does not match incident pins")
                by_incident[trace.incident_id] = trace
                seen_trace_ids.add(trace.trace_id)
            expected = len(agent_incidents)
            ordered_traces = tuple(
                by_incident[incident_id]
                for incident_id in agent_incidents
                if incident_id in by_incident
            )
            trace_payload = TraceEvidenceObservation(
                as_of=now,
                availability="AVAILABLE" if len(ordered_traces) == expected else "PARTIAL",
                expected_count=expected,
                traces=ordered_traces,
            )
    else:
        trace_payload = MissingRepairEvidence(
            as_of=now, input_name="traces", reason="TRACE_ARCHIVE_NOT_CONNECTED"
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
        "traces": (trace_payload, "HEALTH_READ"),
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

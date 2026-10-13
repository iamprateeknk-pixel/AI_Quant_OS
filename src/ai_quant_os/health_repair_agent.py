"""A16 reports repair readiness blockers without creating or running a repair."""

from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import Field

from ai_quant_os.agent_context import CompiledContext, verify_context
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.health_repair_sources import (
    DetailedIncidentContextObservation,
    HealthObservation,
    INPUT_NAMES,
    IncidentContextObservation,
    MissingRepairEvidence,
)


class RepairCandidate(AgentEnvelope):
    agent_id: Literal["A16"] = "A16"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["RepairCandidate"] = "RepairCandidate"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    state: Literal["WAIT_FOR_EVIDENCE"] = "WAIT_FOR_EVIDENCE"
    health_status: Literal["UNAVAILABLE", "RESTORED_ONLY", "MONITORED", "ERROR"]
    open_incidents: int | None = Field(default=None, ge=0, le=10000)
    incident_context_state: Literal["UNAVAILABLE", "SUMMARY_ONLY"]
    repair_candidate_created: Literal[False] = False
    repair_plan: tuple[str, ...] = Field(max_length=8)
    blockers: tuple[str, ...] = Field(min_length=1, max_length=16)
    promotion_allowed: Literal[False] = False

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        health, incident, blockers = _observations(context)
        if (
            self.state != "WAIT_FOR_EVIDENCE"
            or self.health_status != health.status
            or self.open_incidents != health.open_incidents
            or self.incident_context_state != incident.availability
            or self.repair_candidate_created
            or self.repair_plan
            or self.blockers != blockers
        ):
            raise ValueError("Repair readiness output differs from health evidence")


class RepairCandidateV3(AgentEnvelope):
    agent_id: Literal["A16"] = "A16"
    agent_version: Literal["0.3.0"] = "0.3.0"
    output_type: Literal["RepairCandidateV3"] = "RepairCandidateV3"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    state: Literal["WAIT_FOR_EVIDENCE"] = "WAIT_FOR_EVIDENCE"
    health_status: Literal["UNAVAILABLE", "RESTORED_ONLY", "MONITORED", "ERROR"]
    open_incidents: int | None = Field(default=None, ge=0, le=10000)
    incident_context_state: Literal["UNAVAILABLE", "DETAILS", "TRUNCATED"]
    incident_ids: tuple[str, ...] = Field(max_length=8)
    repair_candidate_created: Literal[False] = False
    repair_plan: tuple[str, ...] = Field(max_length=8)
    blockers: tuple[str, ...] = Field(min_length=1, max_length=16)
    promotion_allowed: Literal[False] = False

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        health, incident, blockers = _observations_v3(context)
        if (
            self.state != "WAIT_FOR_EVIDENCE"
            or self.health_status != health.status
            or self.open_incidents != health.open_incidents
            or self.incident_context_state != incident.availability
            or self.incident_ids != tuple(row.incident_id for row in incident.incidents)
            or self.repair_candidate_created
            or self.repair_plan
            or self.blockers != blockers
        ):
            raise ValueError("Repair readiness output differs from health evidence")


def _observations(context: CompiledContext):
    verify_context(context)
    tools = ("HEALTH_READ",) * 4
    if (
        (context.agent_id, context.agent_version, context.scope, context.status)
        != ("A16", "0.2.0", "PAPER", "READY")
        or tuple(row.input_name for row in context.inputs) != INPUT_NAMES
        or tuple(row.source_tool for row in context.inputs) != tools
        or tuple(row.resource_class for row in context.inputs) != ("GENERAL",) * 4
    ):
        raise ValueError("Repair reviewer requires its exact protected paper context")
    health = HealthObservation.model_validate_json(context.inputs[0].payload_json)
    traces = MissingRepairEvidence.model_validate_json(context.inputs[1].payload_json)
    tests = MissingRepairEvidence.model_validate_json(context.inputs[2].payload_json)
    incident = IncidentContextObservation.model_validate_json(context.inputs[3].payload_json)
    if (
        tuple(row.as_of for row in (health, traces, tests, incident))
        != tuple(row.as_of for row in context.inputs)
        or any(row.as_of > context.created_at for row in context.inputs)
        or tuple(row.source_id for row in context.inputs)
        != tuple(
            "health-repair:" + name + ":" + sha256(row.payload_json.encode()).hexdigest()
            for name, row in zip(INPUT_NAMES, context.inputs, strict=True)
        )
        or traces.input_name != "traces"
        or tests.input_name != "code_tests"
        or traces.reason != "TRACE_ARCHIVE_NOT_CONNECTED"
        or tests.reason != "CODE_TEST_RECEIPTS_NOT_CONNECTED"
        or incident.open_incidents != health.open_incidents
        or incident.availability
        != ("UNAVAILABLE" if health.open_incidents is None else "SUMMARY_ONLY")
    ):
        raise ValueError("Repair readiness evidence is stale or inconsistent")
    blockers = {
        "TRACE_EVIDENCE_UNAVAILABLE",
        "CODE_TEST_EVIDENCE_UNAVAILABLE",
        "INCIDENT_DETAILS_UNAVAILABLE",
    }
    if health.status == "UNAVAILABLE":
        blockers.add("HEALTH_EVIDENCE_UNAVAILABLE")
    elif health.status != "MONITORED":
        blockers.add("CONTINUOUS_HEALTH_MONITOR_UNAVAILABLE")
    return health, incident, tuple(sorted(blockers))


def _observations_v3(context: CompiledContext):
    verify_context(context)
    if (
        (context.agent_id, context.agent_version, context.scope, context.status)
        != ("A16", "0.3.0", "PAPER", "READY")
        or tuple(row.input_name for row in context.inputs) != INPUT_NAMES
        or tuple(row.source_tool for row in context.inputs) != ("HEALTH_READ",) * 4
        or tuple(row.resource_class for row in context.inputs) != ("GENERAL",) * 4
    ):
        raise ValueError("Repair reviewer requires its exact protected paper context")
    health = HealthObservation.model_validate_json(context.inputs[0].payload_json)
    traces = MissingRepairEvidence.model_validate_json(context.inputs[1].payload_json)
    tests = MissingRepairEvidence.model_validate_json(context.inputs[2].payload_json)
    incident = DetailedIncidentContextObservation.model_validate_json(
        context.inputs[3].payload_json
    )
    if (
        tuple(row.as_of for row in (health, traces, tests, incident))
        != tuple(row.as_of for row in context.inputs)
        or any(row.as_of > context.created_at for row in context.inputs)
        or tuple(row.source_id for row in context.inputs)
        != tuple(
            "health-repair:" + name + ":" + sha256(row.payload_json.encode()).hexdigest()
            for name, row in zip(INPUT_NAMES, context.inputs, strict=True)
        )
        or health.open_incidents != incident.total_incidents
        or traces.input_name != "traces"
        or tests.input_name != "code_tests"
        or traces.reason != "TRACE_ARCHIVE_NOT_CONNECTED"
        or tests.reason != "CODE_TEST_RECEIPTS_NOT_CONNECTED"
    ):
        raise ValueError("Repair readiness evidence is stale or inconsistent")
    blockers = {"TRACE_EVIDENCE_UNAVAILABLE", "CODE_TEST_EVIDENCE_UNAVAILABLE"}
    if incident.availability in {"UNAVAILABLE", "TRUNCATED"}:
        blockers.add("INCIDENT_DETAILS_UNAVAILABLE")
    if health.status == "UNAVAILABLE":
        blockers.add("HEALTH_EVIDENCE_UNAVAILABLE")
    elif health.status != "MONITORED":
        blockers.add("CONTINUOUS_HEALTH_MONITOR_UNAVAILABLE")
    return health, incident, tuple(sorted(blockers))


async def assess_repair_readiness(invocation: AgentInvocation) -> RepairCandidate:
    context = invocation.context
    health, incident, blockers = _observations(context)
    return RepairCandidate(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        contradictions=(),
        state="WAIT_FOR_EVIDENCE",
        health_status=health.status,
        open_incidents=health.open_incidents,
        incident_context_state=incident.availability,
        repair_candidate_created=False,
        repair_plan=(),
        blockers=blockers,
    )


async def assess_repair_readiness_v3(invocation: AgentInvocation) -> RepairCandidateV3:
    context = invocation.context
    health, incident, blockers = _observations_v3(context)
    return RepairCandidateV3(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        contradictions=(),
        state="WAIT_FOR_EVIDENCE",
        health_status=health.status,
        open_incidents=health.open_incidents,
        incident_context_state=incident.availability,
        incident_ids=tuple(row.incident_id for row in incident.incidents),
        repair_candidate_created=False,
        repair_plan=(),
        blockers=blockers,
    )


def repair_implementation(registry: AgentRegistry, version: str = "0.2.0") -> AgentImplementation:
    if version == "0.3.0":
        return AgentImplementation(
            registry.get("A16", version).manifest_ref,
            RepairCandidateV3,
            assess_repair_readiness_v3,
        )
    return AgentImplementation(
        registry.get("A16", version).manifest_ref,
        RepairCandidate,
        assess_repair_readiness,
    )

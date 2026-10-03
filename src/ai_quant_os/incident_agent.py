"""A18 source-bound incident classification and local operator review proposal."""

from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.incident_policy import IncidentFacts, assess_incident
from ai_quant_os.incident_sources import IncidentEvidence
from ai_quant_os.risk import SafetyState


class WorkflowProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    agent_id: Literal["A18"] = "A18"
    agent_version: Literal["0.2.0"] = "0.2.0"
    policy_version: Literal["paper-incident-review-v1"] = "paper-incident-review-v1"
    context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")
    task_id: str = Field(min_length=1, max_length=128)
    input_refs: tuple[str, ...] = Field(min_length=5, max_length=5)
    created_at: AwareDatetime
    valid_until: AwareDatetime
    incident_class: Literal["CRITICAL", "WARNING", "DEGRADED_EVIDENCE", "NO_NEW_INCIDENT_FOUND"]
    recommended_state: SafetyState
    findings: tuple[str, ...] = Field(max_length=64)
    workflow: Literal[
        "RECONCILE_PAPER_RECORDS", "INSPECT_UNCERTAIN_PAPER_ORDER", "OPERATOR_EVIDENCE_REVIEW"
    ]
    watchdog_incident_count: int | None = Field(ge=0, le=1000)
    status: Literal["AWAITING_OPERATOR_REVIEW"] = "AWAITING_OPERATOR_REVIEW"
    control_authorized: Literal[False] = False
    recovery_authorized: Literal[False] = False
    workflow_dispatched: Literal[False] = False

    @model_validator(mode="after")
    def bounded_review(self):
        if not self.created_at < self.valid_until <= self.created_at + timedelta(seconds=5):
            raise ValueError("Incident proposal exceeds context validity")
        if len(set(self.input_refs)) != 5 or any(
            not ref.startswith("context-evidence:") or len(ref) != 81 for ref in self.input_refs
        ):
            raise ValueError("Invalid incident proposal input references")
        if self.findings != tuple(sorted(set(self.findings))) or any(
            not code or len(code) > 64 for code in self.findings
        ):
            raise ValueError("Invalid incident finding codes")
        return self

    @property
    def proposal_ref(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return "incident-proposal:" + sha256(raw.encode()).hexdigest()


def read_incident_context(context: CompiledContext) -> IncidentEvidence:
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A18",
        "0.2.0",
        "PAPER",
        "READY",
    ):
        raise ValueError("Incident mission requires its complete paper context")
    evidence = IncidentEvidence.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    world = evidence.data_health.world
    if world.as_of > context.created_at or context.valid_until > world.valid_until:
        raise ValueError("Incident context exceeds world observation validity")
    return evidence


def proposal_for_context(context: CompiledContext) -> WorkflowProposal:
    evidence = read_incident_context(context)
    health, broker, recon, risk = (
        evidence.watchdog_events,
        evidence.broker_health,
        evidence.reconciliation,
        evidence.risk_state,
    )
    facts = IncidentFacts(
        live_state=risk.live_state,
        durable_state=risk.durable_state,
        watchdog_available=health.status != "UNAVAILABLE",
        watchdog_error=health.status == "ERROR",
        watchdog_codes=tuple(code for code, count in (health.code_counts or {}).items() if count),
        future_incidents=health.future_count or 0,
        broker_available=broker.enabled and broker.connection_state == "CONNECTED",
        uncertain_orders=broker.uncertain_orders,
        quote_statuses=tuple(row.quote_status for row in evidence.data_health.world.instruments),
        history_statuses=tuple(
            row.history_status for row in evidence.data_health.world.instruments
        ),
        reconciliation_matches=recon.matches,
        future_fills=recon.future_fill_count,
    )
    assessment = assess_incident(facts)
    return WorkflowProposal(
        context_ref=context.context_ref,
        task_id=context.task_id,
        input_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        watchdog_incident_count=health.incident_count,
        **asdict(assessment),
    )


class IncidentRecommendation(AgentEnvelope):
    agent_id: Literal["A18"] = "A18"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["IncidentRecommendation"] = "IncidentRecommendation"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    hard_gate_owner: Literal["RiskKernel"] = "RiskKernel"
    proposal_ref: str = Field(pattern=r"^incident-proposal:[a-f0-9]{64}$")
    incident_class: Literal["CRITICAL", "WARNING", "DEGRADED_EVIDENCE", "NO_NEW_INCIDENT_FOUND"]
    recommended_state: SafetyState
    findings: tuple[str, ...] = Field(max_length=64)
    workflow: Literal[
        "RECONCILE_PAPER_RECORDS", "INSPECT_UNCERTAIN_PAPER_ORDER", "OPERATOR_EVIDENCE_REVIEW"
    ]
    watchdog_incident_count: int | None = Field(ge=0, le=1000)
    recovery_authorized: Literal[False] = False
    workflow_dispatched: Literal[False] = False
    may_add_risk: Literal[False] = False

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        expected = proposal_for_context(context)
        if (
            self.proposal_ref,
            self.incident_class,
            self.recommended_state,
            self.findings,
            self.workflow,
            self.watchdog_incident_count,
        ) != (
            expected.proposal_ref,
            expected.incident_class,
            expected.recommended_state,
            expected.findings,
            expected.workflow,
            expected.watchdog_incident_count,
        ):
            raise ValueError("Incident recommendation differs from retained source facts")


async def classify_incident(invocation: AgentInvocation) -> IncidentRecommendation:
    context = invocation.context
    proposal = proposal_for_context(context)
    response = await invocation.tools.call("INCIDENT_PROPOSE", {})
    if response != {"proposal_ref": proposal.proposal_ref, "status": "AWAITING_OPERATOR_REVIEW"}:
        raise ValueError("Incident proposal receipt mismatch")
    return IncidentRecommendation(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        contradictions=tuple(
            sorted(
                set(
                    (
                        *proposal.findings,
                        "CALIBRATION_UNAVAILABLE",
                        "LIVE_BROKER_HEALTH_UNAVAILABLE",
                    )
                )
            )
        ),
        trace_id=str(uuid4()),
        proposal_ref=proposal.proposal_ref,
        incident_class=proposal.incident_class,
        recommended_state=proposal.recommended_state,
        findings=proposal.findings,
        workflow=proposal.workflow,
        watchdog_incident_count=proposal.watchdog_incident_count,
    )


def incident_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A18", "0.2.0").manifest_ref, IncidentRecommendation, classify_incident
    )

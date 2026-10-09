"""A14 advisory paper execution-cost drift; missing causal evidence remains unassessed."""

from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import Field

from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.drift_sources import report_for_context
from ai_quant_os.execution_drift import ExecutionDriftReport
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation


def _gaps(report: ExecutionDriftReport) -> tuple[str, ...]:
    gaps = [
        "CALIBRATION_UNAVAILABLE",
        "LIVE_RESULTS_UNAVAILABLE",
        "SHADOW_RESULTS_UNAVAILABLE",
        "PREDICTIONS_UNAVAILABLE",
        "SIGNAL_REGIME_SIZING_DATA_CAUSES_UNASSESSED",
    ]
    if report.state == "POLICY_UNCONFIGURED":
        gaps.append("DRIFT_POLICY_UNCONFIGURED")
    if not report.assessments:
        gaps.append("NO_COMPARABLE_EXECUTION_WINDOWS")
    return tuple(gaps)


class DriftAttribution(AgentEnvelope):
    agent_id: Literal["A14"] = "A14"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["DriftAttribution"] = "DriftAttribution"
    confidence: Decimal = Field(ge=0, le=0, allow_inf_nan=False)
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    observation_scope: Literal["LOCAL_PAPER_EXECUTION"] = "LOCAL_PAPER_EXECUTION"
    calibration_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    causal_attribution_established: Literal[False] = False
    demotion_recommendation: Literal["UNASSESSED"] = "UNASSESSED"
    may_add_risk: Literal[False] = False
    report: ExecutionDriftReport

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        if self.report != report_for_context(context) or self.contradictions != _gaps(self.report):
            raise ValueError("Drift output differs from original source comparison")


async def assess_drift(invocation: AgentInvocation) -> DriftAttribution:
    context = invocation.context
    report = report_for_context(context)
    return DriftAttribution(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        confidence=Decimal(0),
        uncertainty=Decimal(1),
        created_at=context.created_at,
        valid_until=context.valid_until,
        contradictions=_gaps(report),
        trace_id=str(uuid4()),
        report=report,
    )


def drift_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A14", "0.2.0").manifest_ref, DriftAttribution, assess_drift
    )

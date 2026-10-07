"""A10 emits task-bound advisory summaries of checked historical validation."""

from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.validation_scientist_contracts import ValidationSummary
from ai_quant_os.validation_scientist_sources import summary_for_context


class ValidationScorecard(AgentEnvelope):
    agent_id: Literal["A10"] = "A10"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["ValidationScorecard"] = "ValidationScorecard"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    summary: ValidationSummary
    isolation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator("isolation_verified", "promotion_allowed", mode="before")
    @classmethod
    def no_authority(cls, value):
        if value is not False:
            raise ValueError("Validation agent cannot grant authority")
        return value

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        expected = summary_for_context(context)
        if (
            self.summary,
            self.evidence_refs,
            self.created_at,
            self.valid_until,
            self.contradictions,
        ) != (
            expected,
            context.input_refs,
            context.created_at,
            context.valid_until,
            expected.reasons,
        ):
            raise ValueError("Validation output differs from original context evidence")


async def validate_candidate(invocation: AgentInvocation) -> ValidationScorecard:
    context = invocation.context
    summary = summary_for_context(context)
    receipt = await invocation.tools.call("VALIDATION_RUN", {})
    if receipt != {
        "artifact_ref": summary.artifact_ref,
        "summary_sha256": summary.summary_sha256,
        "status": "CHECKED_ARCHIVED_OBSERVATION",
    }:
        raise ValueError("Validation result receipt mismatch")
    return ValidationScorecard(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        contradictions=summary.reasons,
        summary=summary,
    )


def validation_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A10", "0.2.0").manifest_ref, ValidationScorecard, validate_candidate
    )

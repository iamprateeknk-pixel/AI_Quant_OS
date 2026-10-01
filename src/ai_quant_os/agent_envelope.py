"""Shared mission envelope v2; specialized fast/deep v1 wire contracts stay unchanged."""

from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import CompiledContext


class AgentEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["2.0"] = "2.0"
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    agent_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    task_id: str = Field(min_length=1, max_length=128)
    input_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    confidence: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    uncertainty: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    created_at: AwareDatetime
    valid_until: AwareDatetime
    contradictions: tuple[str, ...] = Field(max_length=64)
    trace_id: str = Field(min_length=1, max_length=128)
    requires_escalation: Literal[True] = True

    @model_validator(mode="after")
    def bounded_evidence(self) -> "AgentEnvelope":
        if self.valid_until <= self.created_at or self.confidence + self.uncertainty != 1:
            raise ValueError("Invalid mission confidence or expiry")
        for refs in (self.input_refs, self.evidence_refs, self.contradictions):
            if len(set(refs)) != len(refs) or any(not ref or len(ref) > 256 for ref in refs):
                raise ValueError("Invalid mission references")
        return self

    def require_context(self, context: CompiledContext) -> None:
        if (
            (self.agent_id, self.agent_version, self.task_id, self.input_refs)
            != (context.agent_id, context.agent_version, context.task_id, context.input_refs)
            or not set(self.evidence_refs).issubset(context.input_refs)
            or self.created_at < context.created_at
            or self.valid_until > context.valid_until
        ):
            raise ValueError("Mission output context binding failed")

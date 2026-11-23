"""Non-sensitive, durable evidence for a governed agent model route."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class AgentExecutionRoute(BaseModel):
    """One bounded model-routing outcome; prompts and context are never stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    agent_id: str = Field(pattern=r"^A(?:0[1-9]|1[0-8])$")
    agent_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    task_id: str = Field(min_length=1, max_length=128)
    squad: str = Field(min_length=1, max_length=64)
    criticality: Literal["low", "material", "critical", "incident"]
    kind: Literal["llm", "deterministic"]
    governor_decision: Literal["ADMIT", "DOWNGRADE", "DEFER", "DENY"]
    routed_tier: Literal["DETERMINISTIC", "ROUTINE", "DEEP", "FRONTIER"]
    execution_path: Literal["DETERMINISTIC", "PROVIDER"]
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    attempted_provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    fallback_reasons: tuple[
        Literal[
            "BUDGET_DEFER",
            "BUDGET_DENY",
            "TOKEN_STORM",
            "PROVIDER_UNAVAILABLE",
            "PROVIDER_FAILED",
        ],
        ...,
    ] = Field(max_length=4)
    input_tokens: StrictInt | None = Field(default=None, ge=0, le=10_000_000)
    output_tokens: StrictInt | None = Field(default=None, ge=0, le=10_000_000)

    @model_validator(mode="after")
    def consistent_execution(self):
        if self.execution_path == "PROVIDER":
            if (
                self.kind != "llm"
                or self.routed_tier == "DETERMINISTIC"
                or self.provider_id is None
                or self.attempted_provider_id != self.provider_id
                or self.fallback_reasons
            ):
                raise ValueError("Provider route evidence is inconsistent")
        elif self.provider_id is not None:
            raise ValueError("Deterministic route cannot claim an executing provider")
        if self.attempted_provider_id is None and self.fallback_reasons:
            if self.fallback_reasons not in (
                ("BUDGET_DEFER",),
                ("BUDGET_DENY",),
                ("TOKEN_STORM",),
                ("PROVIDER_UNAVAILABLE",),
            ):
                raise ValueError("Fallback reasons require a provider attempt")
        if self.attempted_provider_id is not None and self.execution_path == "DETERMINISTIC":
            if self.fallback_reasons not in (("PROVIDER_FAILED",),):
                raise ValueError("Provider attempt fallback evidence is inconsistent")
        if (self.input_tokens is None) != (self.output_tokens is None):
            raise ValueError("Provider token usage must be complete or absent")
        if self.execution_path == "DETERMINISTIC" and self.input_tokens is not None:
            raise ValueError("Deterministic execution cannot claim provider token usage")
        return self

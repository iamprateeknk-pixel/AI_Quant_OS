"""Protected observations and explicit configuration of routine tool-call quotas."""

import os
from pathlib import Path
from typing import Callable, Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_call_policy import AgentCallPolicy
from ai_quant_os.agent_call_budgets import AgentCallBudget
from ai_quant_os.agent_permissions import ROLE_CEILINGS


class AgentCallCounters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    daily_calls: int = Field(strict=True, ge=0, le=100000)
    monthly_calls: int = Field(strict=True, ge=0, le=100000)
    daily_remaining: int = Field(strict=True, ge=0, le=100000)
    monthly_remaining: int = Field(strict=True, ge=0, le=100000)
    daily_recovery_reserve: int = Field(strict=True, ge=0, le=100000)
    monthly_recovery_reserve: int = Field(strict=True, ge=0, le=100000)


class AgentCallBudgetStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    state: Literal["ENFORCED", "DISABLED", "UNAVAILABLE"]
    accounting_basis: Literal["RESERVED_ROUTINE_TOOL_CALL_ATTEMPTS"] = (
        "RESERVED_ROUTINE_TOOL_CALL_ATTEMPTS"
    )
    recovery_spending_enabled: Literal[False] = False
    provider_token_accounting: Literal[False] = False
    billable_cost_accounting: Literal[False] = False
    day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    head_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    agents: tuple[AgentCallCounters, ...] = Field(max_length=18)

    @model_validator(mode="after")
    def coherent(self):
        metadata = (self.day, self.month, self.head_hash, self.policy_sha256)
        if self.state == "ENFORCED":
            if (
                any(value is None for value in metadata)
                or self.day[:7] != self.month
                or len(self.agents) != 18
                or {row.agent_id for row in self.agents} != set(ROLE_CEILINGS)
            ):
                raise ValueError("Enforced call budget requires complete current metadata")
        elif self.agents or any(value is not None for value in metadata):
            raise ValueError("Unavailable call budgets cannot fabricate counters")
        return self


def call_budget_configuration(
    declared: AgentCallPolicy | None, paper_path: Path | None, operator_token: str | None
) -> AgentCallPolicy | None:
    if declared is None and os.getenv("AI_QUANT_AGENT_CALL_POLICY"):
        with Path(os.environ["AI_QUANT_AGENT_CALL_POLICY"]).open("rb") as source:
            declared = AgentCallPolicy.decode(source.read(65537))
    if declared is not None and (
        type(declared) is not AgentCallPolicy or paper_path is None or not operator_token
    ):
        raise ValueError(
            "Agent call quotas require a typed policy, persistent paper storage and operator token"
        )
    return declared


def install_agent_call_routes(
    app: FastAPI, budget: AgentCallBudget, require_operator: Callable
) -> None:
    @app.get("/api/v1/agents/call-budget", dependencies=[Depends(require_operator)])
    def status() -> AgentCallBudgetStatus:
        return AgentCallBudgetStatus.model_validate(budget.status())

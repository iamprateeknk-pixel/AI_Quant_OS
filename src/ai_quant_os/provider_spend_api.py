"""Protected read-only projection of the existing review spending ledger."""

import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Callable, Literal

from fastapi import Depends, FastAPI, Header, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_permissions import ROLE_CEILINGS
from ai_quant_os.openai_reviewer import OpenAIReviewer

NanoUSD = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,23})$", max_length=24)]
Tokens = Annotated[int, Field(strict=True, ge=0, le=40_000_000_000_000)]
Limit = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]


class SpendCounters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    daily_tokens: Tokens
    monthly_tokens: Tokens
    unsettled_tokens: Tokens
    pending_count: int = Field(strict=True, ge=0, le=20000)
    daily_token_limit: Limit
    monthly_token_limit: Limit
    daily_token_remaining: Limit
    monthly_token_remaining: Limit
    daily_token_reserve: Limit
    monthly_token_reserve: Limit
    daily_nano_usd: NanoUSD
    monthly_nano_usd: NanoUSD
    unsettled_nano_usd: NanoUSD
    daily_money_limit: NanoUSD
    monthly_money_limit: NanoUSD
    daily_money_remaining: NanoUSD
    monthly_money_remaining: NanoUSD
    daily_money_reserve: NanoUSD
    monthly_money_reserve: NanoUSD

    @model_validator(mode="after")
    def coherent(self):
        if (
            not self.unsettled_tokens <= self.daily_tokens <= self.monthly_tokens
            or not int(self.unsettled_nano_usd)
            <= int(self.daily_nano_usd)
            <= int(self.monthly_nano_usd)
            or (self.pending_count == 0) != (self.unsettled_tokens == 0)
            or (self.pending_count == 0) != (int(self.unsettled_nano_usd) == 0)
            or self.unsettled_tokens < 128 * self.pending_count
        ):
            raise ValueError("Inconsistent spending holds")
        for (
            daily,
            monthly,
            daily_reserve,
            monthly_reserve,
            daily_used,
            monthly_used,
            daily_remaining,
            monthly_remaining,
            bound,
        ) in (
            (
                self.daily_token_limit,
                self.monthly_token_limit,
                self.daily_token_reserve,
                self.monthly_token_reserve,
                self.daily_tokens,
                self.monthly_tokens,
                self.daily_token_remaining,
                self.monthly_token_remaining,
                10**9,
            ),
            tuple(
                int(getattr(self, name))
                for name in (
                    "daily_money_limit",
                    "monthly_money_limit",
                    "daily_money_reserve",
                    "monthly_money_reserve",
                    "daily_nano_usd",
                    "monthly_nano_usd",
                    "daily_money_remaining",
                    "monthly_money_remaining",
                )
            )
            + (10**15,),
        ):
            if (
                not daily <= monthly <= bound
                or not daily_reserve <= daily
                or not monthly_reserve <= monthly
                or not daily_reserve <= monthly_reserve
                or daily_remaining != max(0, daily - daily_reserve - daily_used)
                or monthly_remaining != max(0, monthly - monthly_reserve - monthly_used)
            ):
                raise ValueError("Inconsistent spending ceilings")
        return self


class SpendObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    provider_id: Literal["OPENAI_RESPONSES"] = "OPENAI_RESPONSES"
    state: Literal["ENFORCED", "BREACHED", "NOT_CONFIGURED", "UNAVAILABLE"]
    accounting: Literal["PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING"] = (
        "PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING"
    )
    billable_cost_accounting: Literal[False] = False
    recovery_spending_enabled: Literal[False] = False
    observed_at: AwareDatetime
    day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    model: str | None = Field(
        default=None, pattern=r"^gpt-[a-z0-9.-]+-\d{4}-\d{2}-\d{2}$", max_length=128
    )
    policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    head_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    agents: tuple[SpendCounters, ...] = Field(max_length=18)

    @model_validator(mode="after")
    def coherent(self):
        metadata = (self.day, self.month, self.model, self.policy_sha256, self.head_hash)
        if self.observed_at.utcoffset().total_seconds() != 0:
            raise ValueError("UTC spending observation required")
        if self.state in ("ENFORCED", "BREACHED"):
            if (
                any(value is None for value in metadata)
                or self.day != self.observed_at.date().isoformat()
                or self.month != self.day[:7]
                or len(self.agents) != 18
                or {row.agent_id for row in self.agents} != set(ROLE_CEILINGS)
            ):
                raise ValueError("Complete coherent spending observation required")
            if self.state == "ENFORCED" and any(
                row.daily_tokens > row.daily_token_limit - row.daily_token_reserve
                or row.monthly_tokens > row.monthly_token_limit - row.monthly_token_reserve
                or int(row.daily_nano_usd)
                > int(row.daily_money_limit) - int(row.daily_money_reserve)
                or int(row.monthly_nano_usd)
                > int(row.monthly_money_limit) - int(row.monthly_money_reserve)
                for row in self.agents
            ):
                raise ValueError("Enforced observation exceeds declared capacity")
        elif self.agents or any(value is not None for value in metadata):
            raise ValueError("Unavailable spending cannot fabricate counters")
        return self


def observe_spending(reviewer: OpenAIReviewer | None, now: datetime) -> SpendObservation:
    now = now.astimezone(timezone.utc)
    try:
        raw = reviewer.spending_observation(now) if reviewer is not None else None
        if raw is None:
            return SpendObservation(state="NOT_CONFIGURED", observed_at=now, agents=())
        rows = []
        for row in raw["agents"]:
            rows.append(
                {
                    key: str(value)
                    if key.endswith("nano_usd")
                    or key.startswith(("daily_money_", "monthly_money_"))
                    else value
                    for key, value in row.items()
                }
            )
        return SpendObservation.model_validate({**raw, "agents": rows, "observed_at": now})
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError, ArithmeticError):
        return SpendObservation(state="UNAVAILABLE", observed_at=now, agents=())


def install_spending_routes(
    app: FastAPI,
    reviewer: OpenAIReviewer | None,
    require_operator: Callable,
    clock: Callable[[], datetime],
) -> None:
    @app.get(
        "/api/v1/providers/review-spending",
        response_model=SpendObservation,
        dependencies=[Depends(require_operator)],
    )
    def read(
        response: Response,
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
    ):
        observation = observe_spending(reviewer, clock())
        require_operator(x_operator_token, x_operator_session)
        response.headers["Cache-Control"] = "no-store"
        return observation

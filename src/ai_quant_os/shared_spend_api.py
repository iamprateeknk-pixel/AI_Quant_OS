"""Protected single-book cost projection with original source and aggregate binding."""

import sqlite3
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Header, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_permissions import ROLE_CEILINGS
from ai_quant_os.contracts import _require_aware
from ai_quant_os.jev_adapter import JevClassifier
from ai_quant_os.openai_reviewer import OpenAIReviewer
from ai_quant_os.provider_spend_api import SpendCounters
from ai_quant_os.shared_spend_policy import SHARED_ACCOUNTING

Hash = str
Provider = Literal["JEV", "OPENAI_RESPONSES"]
Accounting = Literal[
    "JEV_INPUT_ONLY_AT_DECLARED_RATE_CEILING", "PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING"
]


def _complete_rows(rows):
    return len(rows) == 18 and {row.agent_id for row in rows} == set(ROLE_CEILINGS)


def _within_capacity(row):
    return (
        row.daily_tokens <= row.daily_token_limit - row.daily_token_reserve
        and row.monthly_tokens <= row.monthly_token_limit - row.monthly_token_reserve
        and int(row.daily_nano_usd) <= int(row.daily_money_limit) - int(row.daily_money_reserve)
        and int(row.monthly_nano_usd)
        <= int(row.monthly_money_limit) - int(row.monthly_money_reserve)
    )


class ModelSpendObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: Provider
    model: str = Field(min_length=1, max_length=128)
    accounting: Accounting
    policy_sha256: Hash = Field(pattern=r"^[a-f0-9]{64}$")
    agents: tuple[SpendCounters, ...] = Field(min_length=18, max_length=18)

    @model_validator(mode="after")
    def coherent(self):
        jev = self.provider_id == "JEV"
        if (
            not _complete_rows(self.agents)
            or (
                jev
                and (
                    self.model != "jev-1.13.0"
                    or self.accounting != "JEV_INPUT_ONLY_AT_DECLARED_RATE_CEILING"
                )
            )
            or (
                not jev
                and (
                    not re.fullmatch(r"gpt-[a-z0-9.-]+-\d{4}-\d{2}-\d{2}", self.model)
                    or self.accounting != "PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING"
                )
            )
        ):
            raise ValueError("Complete pinned model accounting required")
        return self


class ConfiguredSpendRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: Provider
    model: str = Field(min_length=1, max_length=128)
    purpose: Literal["CLASSIFICATION", "REVIEW"]
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    agent_version: str = Field(pattern=r"^(0|[1-9]\d{0,5})(\.(0|[1-9]\d{0,5})){2}$", max_length=32)


class SharedSpendObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    accounting: Literal[SHARED_ACCOUNTING] = SHARED_ACCOUNTING
    invoice_verified: Literal[False] = False
    recovery_spending_enabled: Literal[False] = False
    state: Literal[
        "ENFORCED",
        "BREACHED",
        "NOT_CONFIGURED",
        "NOT_SHARED",
        "SEPARATE_LEDGERS",
        "INCOMPLETE_CONFIGURATION",
        "UNAVAILABLE",
    ]
    observed_at: AwareDatetime
    book_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    policy_sha256: Hash | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    head_hash: Hash | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    agents: tuple[SpendCounters, ...] = Field(max_length=18)
    models: tuple[ModelSpendObservation, ...] = Field(max_length=8)
    routes: tuple[ConfiguredSpendRoute, ...] = Field(max_length=2)

    @model_validator(mode="after")
    def coherent(self):
        metadata = (self.book_id, self.day, self.month, self.policy_sha256, self.head_hash)
        if self.observed_at.utcoffset().total_seconds() != 0:
            raise ValueError("UTC original observation required")
        if self.state not in ("ENFORCED", "BREACHED"):
            if (
                self.agents
                or self.models
                or self.routes
                or any(value is not None for value in metadata)
            ):
                raise ValueError("Unavailable shared spending cannot infer data")
            return self
        if (
            any(value is None for value in metadata)
            or not _complete_rows(self.agents)
            or self.day != self.observed_at.date().isoformat()
            or self.month != self.day[:7]
            or not self.models
            or len({row.model for row in self.models}) != len(self.models)
            or not self.routes
            or len({row.purpose for row in self.routes}) != len(self.routes)
        ):
            raise ValueError("Complete single-book observation required")
        inventory = {row.model: row.provider_id for row in self.models}
        for route in self.routes:
            if inventory.get(route.model) != route.provider_id or (route.provider_id == "JEV") != (
                route.purpose == "CLASSIFICATION"
            ):
                raise ValueError("Configured source is outside the original model inventory")
        for row in self.agents:
            children = [
                next(child for child in model.agents if child.agent_id == row.agent_id)
                for model in self.models
            ]
            for name in (
                "daily_tokens",
                "monthly_tokens",
                "unsettled_tokens",
                "pending_count",
                "daily_nano_usd",
                "monthly_nano_usd",
                "unsettled_nano_usd",
            ):
                if int(getattr(row, name)) != sum(int(getattr(child, name)) for child in children):
                    raise ValueError("Aggregate differs from original model counters")
        if self.state == "ENFORCED" and any(
            not _within_capacity(row)
            for row in (*self.agents, *(row for model in self.models for row in model.agents))
        ):
            raise ValueError("Enforced observation exceeds model or aggregate capacity")
        return self


@dataclass(frozen=True)
class SpendingSource:
    provider: OpenAIReviewer | JevClassifier

    def read(self):
        store = self.provider.spending_store
        return (
            store,
            store.path if store else None,
            store.anchor["budget_id"] if store else None,
            store.anchor["policy_sha256"] if store else None,
            self.provider.provider_id,
            self.provider.provider_version,
            self.provider.spend_owner,
        )


def _rows(rows):
    return [
        {
            key: str(value)
            if key.endswith("nano_usd") or key.startswith(("daily_money_", "monthly_money_"))
            else value
            for key, value in row.items()
        }
        for row in rows
    ]


def observe_shared_spending(
    sources: tuple[SpendingSource, ...], now: datetime
) -> SharedSpendObservation:
    _require_aware(now)
    now = now.astimezone(timezone.utc)

    def empty(state):
        return SharedSpendObservation(state=state, observed_at=now, agents=(), models=(), routes=())

    try:
        initial = tuple(source.read() for source in sources)
        if not initial or all(row[0] is None for row in initial):
            return empty("NOT_CONFIGURED")
        if any(row[0] is None for row in initial):
            return empty("INCOMPLETE_CONFIGURATION")
        if len({row[1] for row in initial}) != 1:
            return empty("SEPARATE_LEDGERS")
        raw = initial[0][0].shared_observation(now)
        if initial != tuple(source.read() for source in sources):
            return empty("UNAVAILABLE")
        if raw is None:
            return empty("NOT_SHARED")
        if any(row[2] != raw["book_id"] or row[3] != raw["policy_sha256"] for row in initial):
            return empty("UNAVAILABLE")
        models = [
            {
                **row,
                "provider_id": "JEV"
                if row["accounting"] == "JEV_INPUT_ONLY_AT_DECLARED_RATE_CEILING"
                else "OPENAI_RESPONSES",
                "agents": _rows(row["agents"]),
            }
            for row in raw["models"]
        ]
        routes = [
            {
                "provider_id": row[4],
                "model": row[5],
                "purpose": "CLASSIFICATION" if row[4] == "JEV" else "REVIEW",
                "agent_id": row[6].agent_id,
                "agent_version": row[6].agent_version,
            }
            for row in initial
        ]
        return SharedSpendObservation.model_validate(
            {
                **raw,
                "observed_at": now,
                "agents": _rows(raw["agents"]),
                "models": models,
                "routes": sorted(routes, key=lambda row: row["purpose"]),
            }
        )
    except (
        ValueError,
        OSError,
        sqlite3.Error,
        TypeError,
        KeyError,
        AttributeError,
        ArithmeticError,
    ):
        return empty("UNAVAILABLE")


def install_shared_spending_routes(
    app: FastAPI,
    sources: tuple[SpendingSource, ...],
    require_operator: Callable,
    clock: Callable[[], datetime],
) -> None:
    @app.get(
        "/api/v1/providers/shared-spending",
        response_model=SharedSpendObservation,
        dependencies=[Depends(require_operator)],
    )
    def read(
        response: Response,
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
    ):
        observation = observe_shared_spending(sources, clock())
        require_operator(x_operator_token, x_operator_session)
        response.headers["Cache-Control"] = "no-store"
        return observation

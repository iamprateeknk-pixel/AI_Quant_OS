"""Authenticated history and review receipts for original monitoring observations."""

from datetime import datetime
import sqlite3
from typing import Callable, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, UUID4

from ai_quant_os.monitoring_journal import (
    MonitoringAcknowledgement,
    MonitoringEpisode,
    MonitoringJournalConflict,
    MonitoringJournal,
)


class MonitoringAcknowledgementInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    episode_id: UUID
    action_id: UUID4
    expected_head: str = Field(pattern=r"^[a-f0-9]{64}$")


class MonitoringAcknowledgementView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "1.0"
    mode: str = "PAPER"
    committed: bool = True
    control_authorized: bool = False
    action_id: UUID4
    episode_id: UUID
    actor_session_id: UUID4
    acknowledged_at: AwareDatetime
    source_head: str = Field(pattern=r"^[a-f0-9]{64}$")


class MonitoringEpisodeView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    episode_id: UUID
    rule_name: Literal["PaperHttpScrapeFailed", "PaperHttpScrapeMissing"]
    first_observed_at: AwareDatetime
    active_since: AwareDatetime
    state: Literal[
        "PENDING",
        "FIRING",
        "NOT_CURRENTLY_OBSERVED",
        "OBSERVED_INACTIVE",
        "OBSERVED_REPLACED",
    ]
    last_observed_at: AwareDatetime
    runbook_path: Literal[
        "docs/runbooks/paper-http-monitoring.md#scrape-failed",
        "docs/runbooks/paper-http-monitoring.md#scrape-missing",
    ]
    acknowledgement: MonitoringAcknowledgementView | None


class MonitoringHistoryView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "1.0"
    mode: str = "PAPER"
    source_kind: str = "LOCAL_PROMETHEUS_RULE_EVALUATION"
    durable: bool = True
    notification_verified: bool = False
    independently_attested: bool = False
    head_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    episodes: tuple[MonitoringEpisodeView, ...] = Field(max_length=8192)


def _receipt(value: MonitoringAcknowledgement):
    return MonitoringAcknowledgementView(
        action_id=value.action_id,
        episode_id=value.episode_id,
        actor_session_id=value.actor_session_id,
        acknowledged_at=value.acknowledged_at,
        source_head=value.source_head,
    )


def _episode(value: MonitoringEpisode):
    return MonitoringEpisodeView(
        episode_id=value.episode_id,
        rule_name=value.rule_name,
        first_observed_at=value.first_observed_at,
        active_since=value.active_since,
        state=value.state,
        last_observed_at=value.last_observed_at,
        runbook_path=value.runbook_path,
        acknowledgement=_receipt(value.acknowledgement) if value.acknowledgement else None,
    )


def install_monitoring_journal_routes(
    app: FastAPI,
    journal: MonitoringJournal | None,
    require_session: Callable,
    authenticate_session: Callable,
    clock: Callable[[], datetime],
):
    def require_journal():
        if journal is None:
            raise HTTPException(503, "Persistent monitoring journal is unavailable")
        return journal

    @app.get(
        "/api/v1/observability/incidents",
        response_model=MonitoringHistoryView,
        dependencies=[Depends(require_session)],
    )
    def history(response: Response):
        owned = require_journal()
        try:
            current = owned.history()
            result = MonitoringHistoryView(
                head_hash=current.head_hash,
                episodes=tuple(_episode(item) for item in current.episodes),
            )
        except (ValueError, TypeError, KeyError, OSError, RuntimeError, sqlite3.Error) as error:
            raise HTTPException(503, "Monitoring journal could not be verified") from error
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.post(
        "/api/v1/observability/incidents/acknowledge",
        response_model=MonitoringAcknowledgementView,
        dependencies=[Depends(require_session)],
    )
    def acknowledge(
        command: MonitoringAcknowledgementInput,
        response: Response,
        x_operator_session: str = Header(),
    ):
        owned = require_journal()
        try:
            grant = require_session(x_operator_session)
            receipt = owned.acknowledge(
                command.episode_id,
                action_id=command.action_id,
                actor_session_id=grant.session_id,
                expected_head=command.expected_head,
                now=clock(),
                authorize=lambda: authenticate_session(x_operator_session).session_id,
            )
            result = _receipt(receipt)
        except PermissionError as error:
            raise HTTPException(401, "Operator session expired or invalid") from error
        except MonitoringJournalConflict as error:
            raise HTTPException(409, "Monitoring history changed or action conflicted") from error
        except (ValueError, TypeError, KeyError, OSError, RuntimeError, sqlite3.Error) as error:
            raise HTTPException(503, "Monitoring acknowledgement could not be committed") from error
        response.headers["Cache-Control"] = "no-store"
        return result

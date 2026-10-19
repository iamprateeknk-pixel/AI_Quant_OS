"""Protected health inspection and server-owned scans; no client health verdicts."""

import os
import sqlite3
from dataclasses import asdict
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.health_store import HealthPageChanged
from ai_quant_os.health_timeline_contracts import HealthTimelinePage


class ScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CloseIncidentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=256)


def health_configuration(enabled, path, token):
    setting = os.getenv("AI_QUANT_HEALTH_WATCHDOG_ENABLED", "0")
    if enabled is None:
        if setting not in {"0", "1"}:
            raise ValueError("AI_QUANT_HEALTH_WATCHDOG_ENABLED must be 0 or 1")
        enabled = setting == "1"
    if type(enabled) is not bool or (enabled and (path is None or not token)):
        raise ValueError("Health watchdog requires persistent paper storage and an operator token")
    return enabled


def incident_view(incident, *, evidence=False, transitions=()):
    value = asdict(incident)
    value["detected_at"] = incident.detected_at.isoformat()
    if not evidence:
        value.pop("evidence_json")
    return {
        "schema_version": "1.0",
        **value,
        "incident_id": incident.incident_id,
        "safety_state": incident.safety_state,
        "quarantine": incident.quarantine,
        "status": "CLOSED" if transitions and transitions[-1]["action"] == "CLOSED" else "OPEN",
        "transitions": list(transitions),
        "promotion_allowed": False,
    }


def install_health_routes(app, require_operator, worker, enabled):
    def require_worker():
        if worker is None:
            raise HTTPException(503, "Persistent health watchdog is disabled")
        return worker

    @app.get("/api/v1/paper/health", dependencies=[Depends(require_operator)])
    def status():
        if worker is None:
            return {"schema_version": "1.0", "enabled": False, "state": "DISABLED"}
        try:
            observation = worker.status()
            return {
                **observation,
                "enabled": enabled,
                "state": observation["state"] if enabled else "RESTORED_ONLY",
            }
        except (ValueError, OSError) as error:
            raise HTTPException(503, "Health journal could not be verified") from error

    @app.post("/api/v1/paper/health/scan", dependencies=[Depends(require_operator)])
    def scan(payload: ScanInput = ScanInput()):
        try:
            return require_worker().step()
        except (ValueError, OSError) as error:
            raise HTTPException(503, "Health scan failed closed") from error

    @app.get("/api/v1/paper/health/incidents", dependencies=[Depends(require_operator)])
    def incidents():
        store = require_worker().store
        rows = store.incidents()
        transitions = store.transition_map(item.incident_id for item in rows)
        return {
            "incidents": [
                incident_view(i, transitions=transitions.get(i.incident_id, ())) for i in rows
            ]
        }

    @app.post(
        "/api/v1/paper/health/incidents/{incident_id}/close",
        dependencies=[Depends(require_operator)],
    )
    def close_incident(
        incident_id: str,
        payload: CloseIncidentInput,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ):
        try:
            event = require_worker().close_quote_incident(
                incident_id, command_id=idempotency_key, reason=payload.reason.strip()
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Health incident closure failed closed") from error
        return {"transition": event, "promotion_allowed": False}

    @app.get("/api/v1/paper/health/incidents/page", dependencies=[Depends(require_operator)])
    def incident_page(
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
        head: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
    ) -> HealthTimelinePage:
        if offset and head is None:
            raise HTTPException(422, "Continued page requires the original journal head")
        try:
            page = require_worker().store.incident_page(limit=limit, offset=offset, head=head)
        except HealthPageChanged as error:
            raise HTTPException(
                409, "Health timeline changed; restart at the first page"
            ) from error
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Health journal could not be verified") from error
        transitions = require_worker().store.transition_map(
            item.incident_id for item in page.incidents
        )
        return HealthTimelinePage.model_validate(
            {
                "schema_version": "1.0",
                "mode": "PAPER",
                "head_hash": page.head_hash,
                "total": page.total,
                "offset": page.offset,
                "limit": limit,
                "next_offset": page.next_offset,
                "incidents": [
                    incident_view(incident, transitions=transitions.get(incident.incident_id, ()))
                    for incident in page.incidents
                ],
            }
        )

    @app.get(
        "/api/v1/paper/health/incidents/{incident_id}", dependencies=[Depends(require_operator)]
    )
    def incident(incident_id: str):
        rows = require_worker().store.incidents()
        value = next((i for i in rows if i.incident_id == incident_id), None)
        if value is None:
            raise HTTPException(404, "Unknown health incident")
        return incident_view(
            value, evidence=True, transitions=require_worker().store.transitions(value.incident_id)
        )

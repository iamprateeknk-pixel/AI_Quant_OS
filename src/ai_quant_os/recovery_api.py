"""Authenticated transport for owner-bound paper recovery; SDK is optional."""

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
import os
import re


def recovery_configuration(enabled, target, path, token):
    setting = os.getenv("AI_QUANT_TEMPORAL_RECOVERY_ENABLED", "0")
    if enabled is None:
        if setting not in {"0", "1"}:
            raise ValueError("AI_QUANT_TEMPORAL_RECOVERY_ENABLED must be 0 or 1")
        enabled = setting == "1"
    if type(enabled) is not bool:
        raise ValueError("Temporal recovery enabled must be a boolean")
    target = target if target is not None else os.getenv("AI_QUANT_TEMPORAL_TARGET")
    if enabled:
        match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", target or "")
        if path is None or not token or not match or not 1 <= int(match[1]) <= 65535:
            raise ValueError(
                "Temporal recovery requires persistent paper storage, operator token and local target"
            )
    return enabled, target


class RecoveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class RetryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def install_recovery_routes(app, require_operator):
    def runtime():
        value = app.state.recovery_runtime
        if value is None:
            raise HTTPException(503, "Paper workflow recovery is disabled or unavailable")
        return value

    @app.get("/api/v1/paper/recovery", dependencies=[Depends(require_operator)])
    def status():
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "promotion_allowed": False,
            "state": "READY" if app.state.recovery_runtime is not None else "DISABLED",
        }

    @app.post("/api/v1/paper/recovery", status_code=202, dependencies=[Depends(require_operator)])
    async def start(payload: RecoveryInput):
        return await runtime().start(payload.incident_id, payload.order_id)

    @app.get("/api/v1/paper/recovery/{incident_id}", dependencies=[Depends(require_operator)])
    async def incident(incident_id: str):
        return await runtime().view(incident_id)

    @app.post(
        "/api/v1/paper/recovery/{incident_id}/retry",
        status_code=202,
        dependencies=[Depends(require_operator)],
    )
    async def retry(incident_id: str, payload: RetryInput = RetryInput()):
        return await runtime().retry(incident_id)

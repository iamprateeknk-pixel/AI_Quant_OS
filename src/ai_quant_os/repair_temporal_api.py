"""Operator-authenticated transport for durable, non-promoting repair review."""

from __future__ import annotations

import os
import base64
import re
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from ai_quant_os.temporal_repair import RepairRecoveryRequest


def repair_configuration(
    enabled,
    repository,
    *,
    paper_path,
    operator_token,
    health_enabled,
    recovery_enabled,
    trust_store_path,
    trust_root_public_key,
):
    setting = os.getenv("AI_QUANT_TEMPORAL_REPAIR_ENABLED", "0")
    if enabled is None:
        if setting not in {"0", "1"}:
            raise ValueError("AI_QUANT_TEMPORAL_REPAIR_ENABLED must be 0 or 1")
        enabled = setting == "1"
    if type(enabled) is not bool:
        raise ValueError("Temporal repair enabled must be a boolean")
    if not enabled:
        return False, None, None
    if paper_path is None or not operator_token or not health_enabled or not recovery_enabled:
        raise ValueError(
            "Temporal repair requires persistent health, operator auth, health monitoring and recovery"
        )
    selected = repository or (
        Path(os.environ["AI_QUANT_REPAIR_REPOSITORY"])
        if os.getenv("AI_QUANT_REPAIR_REPOSITORY")
        else Path(__file__).resolve().parents[2]
    )
    if not isinstance(selected, Path) or not selected.is_dir():
        raise ValueError("Temporal repair requires an existing Git repository path")
    store_path = trust_store_path or (
        Path(os.environ["AI_QUANT_REPAIR_VERIFIER_TRUST_STORE"])
        if os.getenv("AI_QUANT_REPAIR_VERIFIER_TRUST_STORE")
        else None
    )
    root_key = trust_root_public_key
    if root_key is None:
        encoded_key = os.getenv("AI_QUANT_REPAIR_TRUST_ROOT_PUBLIC_KEY", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded_key):
            raise ValueError("Temporal repair requires a trusted verifier registry and root key")
        root_key = base64.urlsafe_b64decode(encoded_key + "=")
    if not isinstance(store_path, Path) or not store_path.is_file():
        raise ValueError("Temporal repair requires a trusted verifier registry file")
    from ai_quant_os.repair_certificates import load_repair_verifier_trust_store
    from ai_quant_os.repair_image_inputs import read_repair_image_input

    try:
        raw_store = read_repair_image_input(store_path, 16384)
        trust_store = load_repair_verifier_trust_store(raw_store, root_public_key=root_key)
    except (OSError, ValueError) as error:
        raise ValueError("Temporal repair trusted verifier registry is invalid") from error
    return True, selected, trust_store


class RepairRecoveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    incident_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_owner: str = Field(pattern=r"^[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")

    def request(self) -> RepairRecoveryRequest:
        from ai_quant_os.temporal_repair import RepairRecoveryRequest

        return RepairRecoveryRequest(self.incident_id, self.candidate_id, self.execution_owner)


def install_repair_temporal_routes(app: FastAPI, require_operator):
    def runtime():
        value = app.state.recovery_runtime
        if not app.state.temporal_repair_enabled or value is None:
            raise HTTPException(503, "Temporal repair recovery is disabled or unavailable")
        return value

    @app.get("/api/v1/repair/recovery", dependencies=[Depends(require_operator)])
    def status(response: Response):
        response.headers["Cache-Control"] = "no-store"
        enabled = bool(app.state.temporal_repair_enabled)
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "state": "READY" if enabled and app.state.recovery_runtime is not None else "DISABLED",
            "promotion_allowed": False,
            "production_released": False,
        }

    @app.post(
        "/api/v1/repair/recovery",
        status_code=202,
        dependencies=[Depends(require_operator)],
    )
    async def start(payload: RepairRecoveryInput):
        return await runtime().start_repair(payload.request(), payload.run_id)

    @app.get(
        "/api/v1/repair/recovery/{incident_id}/{candidate_id}/{run_id}",
        dependencies=[Depends(require_operator)],
    )
    async def view(incident_id: str, candidate_id: str, run_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return await runtime().view_repair(incident_id, candidate_id, run_id)

    @app.post(
        "/api/v1/repair/recovery/{incident_id}/{candidate_id}/{run_id}/reconcile",
        status_code=202,
        dependencies=[Depends(require_operator)],
    )
    async def reconcile(incident_id: str, candidate_id: str, run_id: str):
        return await runtime().reconcile_repair(incident_id, candidate_id, run_id)

    @app.post(
        "/api/v1/repair/recovery/{incident_id}/{candidate_id}/{run_id}/verification-certificate",
        status_code=202,
        dependencies=[Depends(require_operator)],
    )
    async def verification_certificate(
        incident_id: str, candidate_id: str, run_id: str, request: Request
    ):
        content_length = request.headers.get("content-length")
        if content_length is not None and (
            not content_length.isdigit() or int(content_length) > 65536
        ):
            raise HTTPException(413, "Repair certificate exceeds the 64 KiB limit")
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 65536:
                raise HTTPException(413, "Repair certificate exceeds the 64 KiB limit")
            chunks.append(chunk)
        return await runtime().submit_repair_certificate(
            incident_id, candidate_id, run_id, b"".join(chunks)
        )

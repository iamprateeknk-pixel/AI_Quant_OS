"""Operator-only inert validation package publication and metadata inspection."""

from datetime import datetime
import sqlite3
from typing import Callable
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.graph_ledger import GraphRecord
from ai_quant_os.holdout_validator_rpc import (
    HoldoutRequest,
    HoldoutResponse,
    HoldoutRpcError,
    verify_response,
)
from ai_quant_os.validation_package_contracts import ValidationPackageMetadata, ValidationPlan
from ai_quant_os.validation_package_service import ValidationPreparation


class ValidationPackageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    holdout_ref: str = Field(pattern=r"^research-data:[a-f0-9]{64}$")
    plan: ValidationPlan


def install_validation_package_routes(
    app: FastAPI,
    require_operator: Callable,
    *,
    preparation: ValidationPreparation,
    validator_client,
    graph_record: Callable[[str], GraphRecord],
    clock: Callable[[], datetime],
) -> None:
    def checked_metadata(request: HoldoutRequest, response: HoldoutResponse) -> dict:
        if type(response) is not HoldoutResponse or response.package_metadata_json is None:
            raise ValueError("Validation service package metadata is missing")
        verify_response(request, response)
        metadata = ValidationPackageMetadata.model_validate_json(
            response.package_metadata_json, strict=True
        )
        if (
            request.operation == "VALIDATION_PACKAGE_METADATA"
            and metadata.package_ref != request.evidence_ref
        ):
            raise ValueError("Validation service package identity mismatch")
        if request.operation == "VALIDATION_PREPARE" and (
            metadata.graph_run_id != request.evidence_ref
            or metadata.candidate_ref != request.candidate_ref
            or metadata.training.dataset_ref != request.training_ref
            or metadata.holdout.dataset_ref != request.holdout_ref
        ):
            raise ValueError("Validation service package scope mismatch")
        return metadata.model_dump(mode="json")

    @app.post("/api/v1/sandbox/validation/packages", dependencies=[Depends(require_operator)])
    def prepare(payload: ValidationPackageInput) -> dict:
        try:
            request = preparation.prepare_request(
                graph_record(payload.run_id), payload.holdout_ref, payload.plan, now=clock()
            )
            response = validator_client.call(request)
            return checked_metadata(request, response)
        except KeyError as error:
            raise HTTPException(404, "Validation input not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Validation preparation admission denied") from error
        except ValueError as error:
            raise HTTPException(409, "Validation inputs rejected") from error
        except HoldoutRpcError as error:
            if error.error_code == "REQUEST_REFUSED":
                raise HTTPException(409, "Validation inputs rejected") from error
            raise HTTPException(503, "Validation service unavailable") from error
        except (OSError, sqlite3.Error, ConnectionError, TimeoutError) as error:
            raise HTTPException(503, "Validation input archive unavailable") from error

    @app.get(
        "/api/v1/sandbox/validation/packages/{package_ref}",
        dependencies=[Depends(require_operator)],
    )
    def metadata(package_ref: str) -> dict:
        try:
            request_id = uuid4().hex
            request = HoldoutRequest(
                request_id=request_id,
                operation="VALIDATION_PACKAGE_METADATA",
                agent_id="A10",
                agent_version="0.2.0",
                task_id="package-metadata",
                context_ref="context-" + request_id,
                evidence_ref=package_ref,
            )
            response = validator_client.call(request)
            metadata = checked_metadata(request, response)
            if metadata["package_ref"] != package_ref:
                raise ValueError("Validation service package identity mismatch")
            return metadata
        except ValueError as error:
            raise HTTPException(404, "Validation package not found") from error
        except HoldoutRpcError as error:
            if error.error_code == "REQUEST_REFUSED":
                raise HTTPException(404, "Validation package not found") from error
            raise HTTPException(503, "Validation package integrity unavailable") from error
        except (OSError, sqlite3.Error, ConnectionError, TimeoutError) as error:
            raise HTTPException(503, "Validation package integrity unavailable") from error

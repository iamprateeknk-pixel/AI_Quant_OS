"""Operator-only inert validation package publication and metadata inspection."""

from datetime import datetime
import sqlite3
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.graph_ledger import GraphRecord
from ai_quant_os.validation_package_contracts import ValidationPlan
from ai_quant_os.validation_package_service import ValidationPreparation
from ai_quant_os.validation_package_store import ValidationPackageStore


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
    archive: ValidationPackageStore,
    graph_record: Callable[[str], GraphRecord],
    clock: Callable[[], datetime],
) -> None:
    @app.post("/api/v1/sandbox/validation/packages", dependencies=[Depends(require_operator)])
    def prepare(payload: ValidationPackageInput) -> dict:
        try:
            package = preparation.prepare(
                graph_record(payload.run_id), payload.holdout_ref, payload.plan, now=clock()
            )
        except KeyError as error:
            raise HTTPException(404, "Validation input not found") from error
        except PermissionError as error:
            raise HTTPException(403, "Validation preparation admission denied") from error
        except ValueError as error:
            raise HTTPException(409, "Validation inputs rejected") from error
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Validation input archive unavailable") from error
        return package.metadata()

    @app.get(
        "/api/v1/sandbox/validation/packages/{package_ref}",
        dependencies=[Depends(require_operator)],
    )
    def metadata(package_ref: str) -> dict:
        try:
            package = archive.get(package_ref)
            if package is None:
                raise KeyError("Validation package not found")
        except KeyError as error:
            raise HTTPException(404, "Validation package not found") from error
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Validation package integrity unavailable") from error
        return package.metadata()

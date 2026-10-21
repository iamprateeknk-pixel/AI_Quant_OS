"""Operator classification of retained research CSV; no evaluation or agent execution."""

from datetime import datetime
import sqlite3
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog


class ResearchDataPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
    resource_class: Literal["TRAINING"]
    csv: str = Field(min_length=1, max_length=1048576)


def install_research_data_routes(
    app: FastAPI,
    catalog: ResearchEvidenceCatalog,
    require_operator: Callable,
    clock: Callable[[], datetime],
) -> None:
    @app.post("/api/v1/paper/research/datasets", dependencies=[Depends(require_operator)])
    def publish(payload: ResearchDataPublication) -> dict:
        try:
            receipt = catalog.publish(
                payload.csv.encode("utf-8"),
                source_id=payload.source_id,
                resource_class=payload.resource_class,
                received_at=clock(),
            )
        except ValueError as error:
            raise HTTPException(409, "Research data classification refused") from error
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Research data catalog unavailable") from error
        return {"mode": "PAPER", "receipt": receipt.model_dump(mode="json")}

    @app.get("/api/v1/research/datasets", dependencies=[Depends(require_operator)])
    def classified_metadata() -> dict:
        try:
            rows = catalog.list()
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Research data catalog integrity unavailable") from error
        return {
            "mode": "PAPER",
            "scope": "CLASSIFIED_METADATA_ONLY",
            "datasets": [row.model_dump(mode="json") for row in rows],
        }

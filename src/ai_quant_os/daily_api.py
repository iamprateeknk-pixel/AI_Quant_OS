"""Operator-protected captures and publication from server-owned paper sources."""

from datetime import date
from pathlib import Path
import sqlite3

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.daily_evidence import DailyEvidence, EvidenceCheckpoint, build_daily_report
from ai_quant_os.evidence_store import EvidenceStore, encode_evidence


class CheckpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checkpoint_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class DailyPublicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date
    opening_checkpoint_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    closing_checkpoint_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def install_daily_routes(
    app, paper_path: Path | None, require_operator, ledger, quotes, catalog, clock
):
    def store():
        if paper_path is None:
            raise HTTPException(503, "Persistent paper evidence is disabled")
        return EvidenceStore(paper_path.with_name(paper_path.stem + ".evidence.sqlite3"))

    def response(value):
        return Response(encode_evidence(value), media_type="application/json")

    @app.post("/api/v1/paper/evidence/checkpoints", dependencies=[Depends(require_operator)])
    def capture(payload: CheckpointInput):
        if payload.checkpoint_id.startswith("daily-auto-"):
            raise HTTPException(409, "Automatic boundary identifiers are reserved")
        try:
            archive = store()
            existing = archive.get_checkpoint(payload.checkpoint_id)
            if existing is not None:
                return response(existing)
            now = clock()
            owners, fills = ledger()
            checkpoint = EvidenceCheckpoint(
                payload.checkpoint_id, now, owners, fills, quotes(), catalog
            )
            return response(archive.checkpoint(checkpoint))
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Paper evidence capture could not be verified") from error

    @app.post("/api/v1/paper/evidence/daily", dependencies=[Depends(require_operator)])
    def publish(payload: DailyPublicationInput):
        try:
            archive = store()
            opening = archive.get_checkpoint(payload.opening_checkpoint_id)
            closing = archive.get_checkpoint(payload.closing_checkpoint_id)
            if opening is None or closing is None:
                raise HTTPException(404, "Unknown paper checkpoint")
            report = build_daily_report(payload.day, opening, closing)
            evidence = DailyEvidence(opening, closing, report, clock())
            return response(archive.publish(evidence))
        except ValueError as error:
            raise HTTPException(
                409, "Daily evidence window, lineage or publication conflict"
            ) from error
        except (TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily paper evidence could not be verified") from error

    @app.get("/api/v1/paper/evidence/daily/{day}", dependencies=[Depends(require_operator)])
    def daily(day: date):
        try:
            value = store().daily(day)
            if value is None:
                raise HTTPException(404, "Unknown daily paper evidence")
            return response(value)
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily paper evidence could not be verified") from error

"""Operator-protected captures and publication from server-owned paper sources."""

from datetime import date
from pathlib import Path
import sqlite3

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.daily_evidence import DailyEvidence, EvidenceCheckpoint, build_daily_report
from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
from ai_quant_os.daily_execution_aggregation import aggregate_daily_execution
from ai_quant_os.evidence_store import (
    EvidenceConflictError,
    EvidenceIntegrityError,
    EvidenceStore,
    encode_evidence,
)


class CheckpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checkpoint_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class DailyPublicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date
    opening_checkpoint_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    closing_checkpoint_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def install_daily_routes(
    app, paper_path: Path | None, require_operator, ledger, execution_ledger, quotes, catalog, clock
):
    def store():
        if paper_path is None:
            raise HTTPException(503, "Persistent paper evidence is disabled")
        return EvidenceStore(paper_path.with_name(paper_path.stem + ".evidence.sqlite3"))

    def response(value):
        return Response(
            encode_evidence(value),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

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

    @app.get(
        "/api/v1/paper/evidence/daily-execution-attribution",
        dependencies=[Depends(require_operator)],
    )
    def daily_execution_aggregate(start: date, end: date):
        try:
            archive = store()
            evidence = archive.daily_execution_range(start, end)
            report = aggregate_daily_execution(start, end, evidence)
            return response(report)
        except EvidenceIntegrityError as error:
            raise HTTPException(503, "Daily execution attribution could not be verified") from error
        except ValueError as error:
            raise HTTPException(422, "Daily execution attribution range is invalid") from error
        except (TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution attribution could not be verified") from error

    @app.post(
        "/api/v1/paper/evidence/daily/{day}/execution-attribution",
        dependencies=[Depends(require_operator)],
    )
    def publish_execution_attribution(day: date):
        try:
            archive = store()
            prior = archive.daily_execution(day)
            daily_value = archive.daily(day)
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution evidence could not be verified") from error
        if prior is not None:
            return response(prior)
        if daily_value is None:
            raise HTTPException(404, "Unknown daily paper evidence")
        try:
            owners, fills, benchmarks = execution_ledger()
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Paper execution ledger could not be verified") from error
        try:
            evidence = build_daily_execution_evidence(
                daily_value,
                owners,
                fills,
                benchmarks,
                published_at=clock(),
            )
        except ValueError as error:
            raise HTTPException(
                409, "Daily execution attribution conflicts with retained evidence"
            ) from error
        try:
            value = archive.publish_daily_execution(evidence)
        except EvidenceIntegrityError as error:
            raise HTTPException(503, "Daily execution evidence could not be verified") from error
        except (EvidenceConflictError, ValueError) as error:
            raise HTTPException(
                409, "Daily execution attribution conflicts with retained evidence"
            ) from error
        except (TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution attribution could not be verified") from error
        return response(value)

    @app.get(
        "/api/v1/paper/evidence/daily/{day}/execution-attribution",
        dependencies=[Depends(require_operator)],
    )
    def daily_execution_attribution(day: date):
        try:
            value = store().daily_execution(day)
            if value is None:
                raise HTTPException(404, "Unknown daily execution attribution")
            return response(value)
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution attribution could not be verified") from error

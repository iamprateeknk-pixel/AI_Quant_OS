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


class DailyShadowComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shadow_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    sequence: int = Field(strict=True, ge=2, le=1000)


def install_daily_routes(
    app,
    paper_path: Path | None,
    require_operator,
    ledger,
    execution_ledger,
    quotes,
    catalog,
    clock,
    research_workspace: Path | None = None,
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

    @app.post(
        "/api/v1/paper/evidence/daily/{day}/shadow-comparison",
        dependencies=[Depends(require_operator)],
    )
    def daily_shadow_comparison(day: date, payload: DailyShadowComparisonInput):
        try:
            daily_execution = store().daily_execution(day)
        except EvidenceIntegrityError as error:
            raise HTTPException(503, "Daily execution evidence could not be verified") from error
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily execution evidence could not be verified") from error
        if daily_execution is None:
            raise HTTPException(404, "Unknown daily execution attribution")
        if research_workspace is None:
            raise HTTPException(503, "Shadow evidence workspace is disabled")
        shadow_path = research_workspace / "shadow.sqlite3"
        if not shadow_path.is_file():
            raise HTTPException(404, "Unknown shadow portfolio")
        try:
            from ai_quant_os.shadow_store import ShadowStore

            shadow_store = ShadowStore(shadow_path)
            if shadow_store.lookup(payload.shadow_id) is None:
                raise HTTPException(404, "Unknown shadow portfolio")
            history = shadow_store.history(payload.shadow_id)
        except HTTPException:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Shadow evidence could not be verified") from error
        if payload.sequence > len(history):
            raise HTTPException(404, "Unknown shadow observation sequence")
        prior, current = history[payload.sequence - 2 : payload.sequence]
        try:
            from ai_quant_os.paper_shadow_comparison import compare_daily_paper_to_shadow

            return response(compare_daily_paper_to_shadow(daily_execution, prior, current))
        except ValueError as error:
            raise HTTPException(
                409, "PAPER/shadow evidence does not link to this UTC day"
            ) from error
        except ArithmeticError as error:
            raise HTTPException(409, "PAPER/shadow amounts cannot be compared exactly") from error
